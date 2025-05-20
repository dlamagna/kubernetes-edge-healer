#!/usr/bin/env python3
import time
import subprocess
import requests
import signal
import sys
import logging
import argparse
import os
from datetime import datetime

# —— CONFIG ——
NODE_IP            = "172.20.0.4"
API_SERVER         = "10.96.0.1"       # ClusterIP of kube-apiserver Service
API_PORT           = "6443"
LABEL_SELECTOR     = "app=busybox-spread"
BUSYBOX_NAMESPACE  = "default"
ITERATIONS         = 3
PAUSE_SECONDS      = 2.0
OUTAGE_DURATION    = 5.0               # seconds to keep the API server blackholed
METRICS_URL        = f"http://{NODE_IP}:8000/metrics"
LOG_DIR            = "debug/logs"

# Timestamped log file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE           = os.path.join(LOG_DIR, f"measure_latency_{timestamp}.log")

# —— PARSE ARGS ——
parser = argparse.ArgumentParser(
    description="Measure edge-healer restore latency with WAN outage simulation."
)
parser.add_argument(
    '-v', '--verbose', action='store_true', help='Enable verbose console logging'
)
args = parser.parse_args()

# —— SETUP LOGGING ——
os.makedirs(LOG_DIR, exist_ok=True)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_level = logging.DEBUG if args.verbose else logging.INFO
console_handler.setLevel(console_level)
console_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
console_handler.setFormatter(console_formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
log = logging.getLogger("measure_latency")


def run_cmd(cmd):
    log.debug(f"Running shell command: {cmd}")
    return subprocess.check_call(cmd, shell=True)


def block_api():
    log.info(f"Blocking API server {API_SERVER}:{API_PORT}")
    run_cmd(
        f"sudo iptables -I OUTPUT "
        f"-p tcp --dport {API_PORT} "
        f"-d {API_SERVER} "
        f"-j DROP"
    )


def unblock_api():
    log.info("Unblocking API server")
    run_cmd(
        f"sudo iptables -D OUTPUT "
        f"-p tcp --dport {API_PORT} "
        f"-d {API_SERVER} "
        f"-j DROP"
    )


def ensure_nft_base():
    subprocess.call("nft list table inet filter >/dev/null 2>&1 || sudo nft add table inet filter", shell=True)
    subprocess.call(
        "nft list chain inet filter output >/dev/null 2>&1 || "
        "sudo nft add chain inet filter output '{ type filter hook output priority 0; policy accept; }'",
        shell=True
    )


def delete_pod_local(pod_name):
    """
    While the API is unreachable, use crictl to kill & remove the pod's container directly on this node.
    """
    log.info(f"Locally deleting pod {pod_name} via crictl")
    # Find the container ID by label
    get_cid = (
        f"crictl ps --state=running "
        f"--label io.kubernetes.pod.name={pod_name} "
        f"-o go-template='{{{{range .containers}}}}{{{{.id}}}}{{{{end}}}}'"
    )
    try:
        cid = subprocess.check_output(get_cid, shell=True).decode().strip()
    except subprocess.CalledProcessError:
        log.error(f"Failed to get container ID for pod {pod_name}")
        return
    if not cid:
        log.error(f"No running container found for pod {pod_name}")
        return

    run_cmd(f"sudo crictl stop {cid}")
    run_cmd(f"sudo crictl rm   {cid}")
    log.info(f"Pod {pod_name} container {cid} stopped & removed")


def get_restore_count():
    log.debug(f"Fetching metrics from {METRICS_URL}")
    try:
        resp = requests.get(METRICS_URL, timeout=2)
    except requests.RequestException as e:
        log.error(f"Error fetching metrics endpoint: {e}")
        return None

    if resp.status_code != 200:
        log.error(f"Metrics endpoint returned HTTP {resp.status_code}")
        return None

    for line in resp.text.splitlines():
        if line.startswith("restore_latency_seconds_count"):
            return int(float(line.split()[-1]))

    log.error("Metric 'restore_latency_seconds_count' not found!")
    return None


def get_pod_name(timeout=30.0, interval=0.5):
    log.info(f"Waiting up to {timeout}s for a Pod with label '{LABEL_SELECTOR}'")
    deadline = time.time() + timeout
    attempt = 1
    while time.time() < deadline:
        try:
            out = subprocess.check_output([
                "kubectl", "get", "pod",
                "-l", LABEL_SELECTOR,
                "-n", BUSYBOX_NAMESPACE,
                "-o", "jsonpath={.items[0].metadata.name}"
            ], stderr=subprocess.DEVNULL)
            pod = out.decode().strip()
            if pod:
                log.info(f"Found Pod: {pod}")
                return pod
        except subprocess.CalledProcessError:
            log.debug(f"Attempt {attempt}: no Pod yet")
        attempt += 1
        time.sleep(interval)
    raise RuntimeError(f"No Pod matching '{LABEL_SELECTOR}' within {timeout}s")


def measure_restore_latency():
    latencies = []
    for i in range(1, ITERATIONS + 1):
        log.info(f"=== Iteration {i}/{ITERATIONS} ===")
        pod = get_pod_name()
        pre_count = get_restore_count()
        if pre_count is None:
            log.error("Cannot read initial restore count — aborting")
            sys.exit(1)

        # 1) Start network outage
        log.info("Step 1: simulate WAN outage")
        block_api()
        time.sleep(OUTAGE_DURATION)

        # 2) Delete the Pod locally while control-plane is down
        log.info(f"Step 2: deleting Pod {pod} locally")
        start = time.perf_counter()
        delete_pod_local(pod)

        # 3) Heal network
        log.info("Step 3: healing WAN")
        unblock_api()

        # 4) Wait for restore counter to increment
        log.info("Step 4: waiting for restore counter to increment")
        while True:
            current = get_restore_count()
            if current is not None and current > pre_count:
                break
            time.sleep(0.01)

        latency = time.perf_counter() - start
        latencies.append(latency)
        log.info(f"Pod {pod} restored in {latency:.3f}s")
        time.sleep(PAUSE_SECONDS)

    latencies.sort()
    def pct(p): return latencies[int(p * len(latencies))]
    log.info("=== Summary ===")
    log.info(f"  p50 = {pct(0.50):.3f}s")
    log.info(f"  p90 = {pct(0.90):.3f}s")
    log.info(f"  p99 = {pct(0.99):.3f}s")


if __name__ == "__main__":
    def cleanup(signum, frame):
        log.warning("Interrupted! Cleaning up.")
        try: unblock_api()
        except: pass
        sys.exit(1)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    ensure_nft_base()
    measure_restore_latency()
