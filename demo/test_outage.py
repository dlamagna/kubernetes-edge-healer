#!/usr/bin/env python3
"""
Measure edge-healer restore latency under WAN outage vs normal pod restart.
Automatically detects a working container runtime CLI (docker, ctr, crictl).
Produces latency histograms with Matplotlib.
"""
import time
import subprocess
import requests
import signal
import sys
import logging
import os
import shutil
from datetime import datetime
import matplotlib.pyplot as plt

# —— CONFIG ——
NODE_IP             = "172.20.0.4"
API_SERVER          = "10.96.0.1"
API_PORT            = "6443"
NAMESPACE           = "default"
LABEL_SELECTOR      = "app=busybox-spread"
ITERATIONS          = 3
PAUSE_SECONDS       = 2.0
OUTAGE_DURATION     = 5.0
RETRY_INTERVAL      = 0.01
LOG_DIR             = "debug/logs"
TIMEFMT             = "%Y%m%d_%H%M%S"
WAN_PLOT            = "wan_latency.png"
NORMAL_PLOT         = "normal_latency.png"
OVERLAY_PLOT        = "overlay_latency.png"
METRIC_NAME         = "restore_latency_seconds_count"  
WORKER_CONTAINER     = "serf-demo-worker2"           # the container/pod that is exec'd into
INTERNAL_SCRIPT_PATH = "scripts/internal-pod-deletion.sh"
DOCKER_DELETION_SCRIPT = "internal-pod-deletion.sh"

# —— PARSE ARGS ——
import argparse
parser = argparse.ArgumentParser(description="Compare WAN restore vs normal restart latency.")
parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logging')
args = parser.parse_args()

# —— SETUP LOGGING ——
os.makedirs(LOG_DIR, exist_ok=True)
timestamp = datetime.now().strftime(TIMEFMT)
log_file = os.path.join(LOG_DIR, f"measure_outage_{timestamp}.log")

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
log = logging.getLogger("test_outage")

# —— RUNTIME DETECTION ——
def detect_runtime():
    candidates = [
        ('crictl', ['crictl', 'ps', '--state=running']),
        ('ctr',    ['ctr', '--namespace', 'k8s.io', 'tasks', 'ls']),
        ('docker', ['docker', 'ps'])
    ]
    for name, cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
                log.info(f"Using runtime CLI: {name}")
                return name
            except Exception:
                log.debug(f"Runtime {name} found but unresponsive, skipping.")
    log.error("No working container runtime CLI found. Install crictl, ctr, or docker.")
    sys.exit(1)

runtime = detect_runtime()
METRICS_URL = f"http://{NODE_IP}:8000/metrics"

# —— UTILITIES ——
def run_cmd(cmd, check=True):
    log.debug(f"Running: {cmd}")
    return subprocess.check_call(cmd, shell=True) if check else subprocess.call(cmd, shell=True)

# —— NETWORK FAULTS ——
def block_api():
    log.info(f"Blocking API server {API_SERVER}:{API_PORT}")
    run_cmd(f"sudo iptables -I OUTPUT -p tcp --dport {API_PORT} -d {API_SERVER} -j DROP")

def unblock_api():
    log.info("Unblocking API server")
    run_cmd(f"sudo iptables -D OUTPUT -p tcp --dport {API_PORT} -d {API_SERVER} -j DROP")

# —— POD DELETION ——
def delete_pod_local(pod_name):
    """
    Delete pod container via CRI. Returns True if deleted.
    """
    log.info(f"Deleting pod {pod_name} via runtime {runtime}")
    label = f"io.kubernetes.pod.name={pod_name}"
    try:
        if runtime == 'crictl':
            out = subprocess.check_output(['crictl','ps','--state=running',f'--label={label}'])
            cid = out.decode().split()[0]
            run_cmd(f"crictl rm -f {cid}")
        elif runtime == 'ctr':
            out = subprocess.check_output(['ctr','--namespace','k8s.io','containers','ls','-q',f'--label={label}'])
            cid = out.decode().strip().split()[0]
            run_cmd(f"ctr task kill --namespace k8s.io --signal SIGKILL {cid}")
        else:
            out = subprocess.check_output(['docker','ps','--filter',f'label={label}','--format','{{.ID}}'])
            cid = out.decode().strip().split()[0]
            run_cmd(f"docker rm -f {cid}")
        log.debug(f"Removed container {cid}")
        return True
    except Exception as e:
        log.error(f"Failed to delete pod via {runtime}: {e}")
        return False

def delete_pod_internal_via_script():
    """
    Copies the internal deletion script into the worker container and executes it.
    """
    # 1) Copy the script into the container
    try:
        subprocess.check_call(
            f"docker cp {INTERNAL_SCRIPT_PATH} {WORKER_CONTAINER}:{DOCKER_DELETION_SCRIPT}",
            shell=True
        )
        subprocess.check_call(
            f"docker exec {WORKER_CONTAINER} chmod +x {DOCKER_DELETION_SCRIPT}",
            shell=True
        )
        log.info(f"Copied script to container at {DOCKER_DELETION_SCRIPT}")
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to copy script into container: {e}")
        return False

    # 2) Execute it
    cmd = f"docker exec {WORKER_CONTAINER} bash -c './{DOCKER_DELETION_SCRIPT}'"
    log.info(f"Running internal pod-deletion script: {cmd}")
    try:
        subprocess.check_call(cmd, shell=True)
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"Internal pod-deletion script failed: {e}")
        return False


# —— METRICS ——
def get_restore_count():
    log.debug(f"Fetching metrics from {METRICS_URL}")
    try:
        r = requests.get(METRICS_URL, timeout=2)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Metrics fetch error: {e}")
        return None
    for line in r.text.splitlines():
        if line.startswith(f"{METRIC_NAME} "):
            try:
                return int(float(line.split()[-1]))
            except ValueError:
                log.error(f"Invalid metric value on line: {line}")
                return None
    log.error(f"Metric '{METRIC_NAME}' not found. Available metrics:\n{r.text}")
    return None

# —— POD DISCOVERY & WAIT ——
def get_pod_name(timeout=30, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.check_output([
                'kubectl','get','pod','-n',NAMESPACE,'-l',LABEL_SELECTOR,
                '-o','jsonpath={.items[0].metadata.name}'
            ], stderr=subprocess.DEVNULL)
            name = out.decode().strip()
            if name:
                log.debug(f"Found pod: {name}")
                return name
        except subprocess.CalledProcessError:
            pass
        time.sleep(interval)
    log.error(f"Pod with label {LABEL_SELECTOR} not found within {timeout}s.")
    sys.exit(1)

def wait_for_running(label, namespace, timeout=30, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        status = subprocess.check_output([
            'kubectl','get','pod','-n',namespace,'-l',label,
            '-o','jsonpath={.items[0].status.phase}'
        ]).decode().strip()
        if status == 'Running':
            return time.time() - start
        time.sleep(interval)
    log.error(f"Pod did not reach Running state within {timeout}s.")
    return None

# —— MAIN ——
def measure_latencies():
    wan_lat, norm_lat = [], []
    for i in range(1, ITERATIONS+1):
        log.info(f"Iteration {i}/{ITERATIONS}")
        pod = get_pod_name()
        pre = get_restore_count()
        if pre is None:
            log.error("Cannot read initial metric, aborting.")
            sys.exit(1)

        # WAN outage experiment
        block_api()
        t0 = time.perf_counter()
        deleted = delete_pod_internal_via_script()
        if not deleted:
            log.warning("Pod deletion failed under WAN outage, skipping iteration.")
            unblock_api()
            time.sleep(PAUSE_SECONDS)
            continue
        else:
            log.info("Pod deleted succesfully")
        time.sleep(OUTAGE_DURATION)
        unblock_api()
        # wait for restore
        while True:
            cur = get_restore_count()
            if cur is not None and cur > pre:
                wan_lat.append(time.perf_counter() - t0)
                break
            time.sleep(RETRY_INTERVAL)
        time.sleep(PAUSE_SECONDS)

        # Normal restart
        pod2 = get_pod_name()
        t1 = time.perf_counter()
        run_cmd(f"kubectl delete pod {pod2} -n {NAMESPACE}")
        lat2 = wait_for_running(LABEL_SELECTOR, NAMESPACE)
        if lat2 is not None:
            norm_lat.append(lat2)
        time.sleep(PAUSE_SECONDS)

    return wan_lat, norm_lat

if __name__ == '__main__':
    def cleanup(signum, frame):
        log.warning("Interrupted, unblocking API.")
        try: unblock_api()
        except: pass
        sys.exit(1)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    wan, normal = measure_latencies()
    if not wan or not normal:
        log.error("Insufficient data collected, exiting.")
        sys.exit(1)

    wan_sorted = sorted(wan)
    norm_sorted = sorted(normal)
    def pct(arr, p): return arr[min(int(p*len(arr)), len(arr)-1)]
    log.info(f"WAN p50={pct(wan_sorted,0.5):.3f}s p90={pct(wan_sorted,0.9):.3f}s p99={pct(wan_sorted,0.99):.3f}s")
    log.info(f"Normal p50={pct(norm_sorted,0.5):.3f}s p90={pct(norm_sorted,0.9):.3f}s p99={pct(norm_sorted,0.99):.3f}s")

    # Plot WAN
    plt.figure()
    plt.hist(wan, bins=20)
    plt.title('WAN Restore Latency')
    plt.xlabel('Latency (s)')
    plt.ylabel('Frequency')
    plt.savefig(WAN_PLOT)
    log.info(f"WAN latency histogram saved to {WAN_PLOT}")

    # Plot Normal
    plt.figure()
    plt.hist(normal, bins=20)
    plt.title('Normal Restart Latency')
    plt.xlabel('Latency (s)')
    plt.ylabel('Frequency')
    plt.savefig(NORMAL_PLOT)
    log.info(f"Normal latency histogram saved to {NORMAL_PLOT}")

    # Overlay
    plt.figure()
    plt.hist(wan, bins=20, alpha=0.5, label='WAN')
    plt.hist(normal, bins=20, alpha=0.5, label='Normal')
    plt.legend()
    plt.title('Overlay Latencies')
    plt.xlabel('Latency (s)')
    plt.ylabel('Frequency')
    plt.savefig(OVERLAY_PLOT)
    log.info(f"Overlay latency histogram saved to {OVERLAY_PLOT}")
