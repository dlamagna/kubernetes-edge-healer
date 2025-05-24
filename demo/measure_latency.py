#!/usr/bin/env python3
"""
Measure edge-healer restore latency with WAN outage simulation (standalone).
Only supports verbose logging via -v/--verbose.
Automatically detects a working container runtime CLI (docker, ctr, crictl).
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

# —— CONFIG ——
NODE_IP        = "172.20.0.4"
API_SERVER     = "10.96.0.1"
API_PORT       = "6443"
NAMESPACE      = "default"
LABEL_SELECTOR = "app=busybox-spread"
ITERATIONS     = 3
PAUSE_SECONDS  = 2.0
OUTAGE_DURATION = 5.0
RETRY_INTERVAL = 0.01
LOG_DIR        = "debug/logs"
TIMEFMT        = "%Y%m%d_%H%M%S"

# —— PARSE ARGS ——
import argparse
parser = argparse.ArgumentParser(description="Measure restore latency (verbose only)")
parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logging')
args = parser.parse_args()

# —— SETUP LOGGING ——
os.makedirs(LOG_DIR, exist_ok=True)

timestamp = datetime.now().strftime(TIMEFMT)
log_file = os.path.join(LOG_DIR, f"measure_latency_{timestamp}.log")

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
log = logging.getLogger("measure_latency")

# —— RUNTIME DETECTION ——
def detect_runtime():
    # Prefer crictl, then ctr, then docker for CRI compatibility
    candidates = [
        ('crictl', ['crictl', 'ps', '--state=running']),
        ('ctr', ['ctr', '--namespace', 'k8s.io', 'tasks', 'ls']),
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
    log.error("No working container runtime CLI found. Install crictl, containerd CLI (ctr), or docker with shim.")
    sys.exit(1)

runtime = detect_runtime()

METRICS_URL = f"http://{NODE_IP}:8000/metrics"

# —— UTILITIES ——

def run_cmd(cmd, check=True):
    log.debug(f"Running command: {cmd}")
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
    Delete a pod's container directly using the pre-detected runtime only.
    Returns True if deletion commands were issued, False if listing fails.
    """
    log.info(f"Attempting deletion of pod {pod_name} via {runtime}")
    # Define commands for the selected runtime
    if runtime == 'crictl':
        list_cmd = ['sudo','crictl','ps','--state=running',f'--label=io.kubernetes.pod.name={pod_name}','-o','go-template={{range .containers}}{{.id}}{{end}}']
        stop_cmd = ['sudo','crictl','stop']
        rm_cmd   = ['sudo','crictl','rm']
    elif runtime == 'ctr':
        list_cmd = ['sudo','ctr','--namespace','k8s.io','tasks','ls','--quiet']
        stop_cmd = ['sudo','ctr','--namespace','k8s.io','tasks','kill']
        rm_cmd   = ['sudo','ctr','--namespace','k8s.io','tasks','rm']
    else:  # docker
        # filter by container name matching pod_name (CRI runtime labels might not reflect pod labels)
        list_cmd = ['sudo','docker','ps','--filter',f'name={pod_name}','--format','{{.ID}}']
        stop_cmd = ['sudo','docker','stop']
        rm_cmd   = ['sudo','docker','rm']
        list_cmd = ['sudo','docker','ps','--filter',f'label=io.kubernetes.pod.name={pod_name}','--format','{{.ID}}']
        stop_cmd = ['sudo','docker','stop']
        rm_cmd   = ['sudo','docker','rm']
    # Try listing
    try:
        out = subprocess.check_output(list_cmd, stderr=subprocess.DEVNULL).decode().strip().splitlines()
        if not out:
            log.warning(f"No running container for pod {pod_name} via {runtime}")
            return False
        cid = out[0]
        log.info(f"Found container {cid} for pod {pod_name}")
    except Exception as e:
        log.error(f"Failed to list containers via {runtime}: {e}")
        return False
    # Delete container
    run_cmd(' '.join(stop_cmd + [cid]))
    run_cmd(' '.join(rm_cmd + [cid]))
    log.info(f"Stopped & removed container {cid}")
    return True

# —— METRICS ——

def get_restore_count():
    log.debug(f"Fetching metrics from {METRICS_URL}")
    try:
        resp = requests.get(METRICS_URL, timeout=2)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Error fetching metrics: {e}")
        return None
    for line in resp.text.splitlines():
        if line.startswith("restore_latency_seconds_count"):
            return int(float(line.split()[-1]))
    log.error("Metric 'restore_latency_seconds_count' not found!")
    return None

# —— POD DISCOVERY ——

def get_pod_name(timeout=30.0, interval=0.5):
    log.info(f"Waiting for pod with label '{LABEL_SELECTOR}'")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = subprocess.check_output([
                "kubectl", "get", "pod",
                "-l", LABEL_SELECTOR,
                "-n", NAMESPACE,
                "-o", "jsonpath={.items[0].metadata.name}"
            ], stderr=subprocess.DEVNULL)
            pod = out.decode().strip()
            if pod:
                log.info(f"Found Pod: {pod}")
                return pod
        except subprocess.CalledProcessError:
            pass
        time.sleep(interval)
    log.error(f"No Pod matching '{LABEL_SELECTOR}' within {timeout}s")
    sys.exit(1)

# —— MAIN ——

def measure_restore_latency():
    latencies = []
    for i in range(1, ITERATIONS+1):
        log.info(f"=== Iteration {i}/{ITERATIONS} ===")
        pod = get_pod_name()
        pre_count = get_restore_count()
        if pre_count is None:
            log.error("Cannot read initial restore count — aborting")
            sys.exit(1)

                # Start outage and attempt deletion during outage window
        block_api()
        start = time.perf_counter()
        deleted = delete_pod_local(pod)
        elapsed = time.perf_counter() - start
        if not deleted:
            # Could not delete pod during outage; skip to next iteration
            log.warning(f"Skipping metrics wait since pod deletion failed for {pod}")
            unblock_api()
            time.sleep(PAUSE_SECONDS)
            continue
        # Sleep remainder of outage window
        time.sleep(max(0, OUTAGE_DURATION - elapsed))
        unblock_api()

        # fetch logs
        ts = datetime.now().strftime(TIMEFMT)
        log_file = os.path.join(LOG_DIR, f"edge_healer_iter{i}_{ts}.log")
        log.info(f"Dumping edge-healer logs to {log_file}")
        run_cmd(f"kubectl logs -n kube-system -l app=edge-healer -c healer --timestamps > {log_file}")

        # wait for restore
        while True:
            current = get_restore_count()
            if current is not None and current > pre_count:
                break
            time.sleep(RETRY_INTERVAL)

        latency = time.perf_counter() - start
        latencies.append(latency)
        log.info(f"Pod {pod} restored in {latency:.3f}s")
        time.sleep(PAUSE_SECONDS)

    if not latencies:
        log.error("No successful latencies recorded — aborting summary.")
        sys.exit(1)

    latencies.sort()
    def pct(p): return latencies[min(int(p * len(latencies)), len(latencies)-1)]
    log.info("=== Summary ===")
    log.info(f"  p50 = {pct(0.5):.3f}s")
    log.info(f"  p90 = {pct(0.9):.3f}s")
    log.info(f"  p99 = {pct(0.99):.3f}s")

if __name__ == '__main__':
    def cleanup(signum, frame):
        log.warning("Interrupted! Cleaning up.")
        try:
            unblock_api()
        except Exception:
            pass
        sys.exit(1)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    measure_restore_latency()
