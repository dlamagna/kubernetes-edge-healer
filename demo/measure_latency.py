#!/usr/bin/env python3
"""
Measure edge-healer restore latency with WAN outage simulation (standalone).
Only supports verbose logging via -v/--verbose.
"""
import time
import subprocess
import requests
import signal
import sys
import logging
import os
from datetime import datetime

# —— CONFIG ——
NODE_IP       = "172.20.0.4"
API_SERVER    = "10.96.0.1"
API_PORT      = "6443"
NAMESPACE     = "default"
LABEL_SELECTOR= "app=busybox-spread"
ITERATIONS    = 3
PAUSE_SECONDS = 2.0
OUTAGE_DURATION = 5.0
RETRY_INTERVAL  = 0.01
LOG_DIR       = "debug/logs"
TIMEFMT       = "%Y%m%d_%H%M%S"

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

METRICS_URL = f"http://{NODE_IP}:8000/metrics"

# —— UTILITIES ——

def run_cmd(cmd, check=True):
    log.debug(f"Running command: {cmd}")
    return subprocess.check_call(cmd, shell=True) if check else subprocess.call(cmd, shell=True)

# —— NETWORK FAULTS ——

def block_api():
    log.info(f"Blocking API server {API_SERVER}:{API_PORT}")
    run_cmd(
        f"sudo iptables -I OUTPUT -p tcp --dport {API_PORT} -d {API_SERVER} -j DROP"
    )


def unblock_api():
    log.info("Unblocking API server")
    run_cmd(
        f"sudo iptables -D OUTPUT -p tcp --dport {API_PORT} -d {API_SERVER} -j DROP"
    )

# —— POD DELETION ——

def delete_pod_local(pod_name):
    log.info(f"Locally deleting pod {pod_name} via crictl")
    get_cid = (
        f"crictl ps --state=running --label io.kubernetes.pod.name={pod_name} "
        "-o go-template='{{range .containers}}{{.id}}{{end}}'"
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
    raise RuntimeError(f"No Pod matching '{LABEL_SELECTOR}' within {timeout}s")

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

        block_api()
        time.sleep(OUTAGE_DURATION)

        log.info(f"Deleting Pod {pod} locally (start)")
        start = time.perf_counter()
        delete_pod_local(pod)
        unblock_api()

        # fetch logs
        ts = datetime.now().strftime(TIMEFMT)
        log_file = os.path.join(LOG_DIR, f"edge_healer_iter{i}_{ts}.log")
        log.info(f"Dumping edge-healer logs to {log_file}")
        run_cmd(
            f"kubectl logs -n kube-system -l app=edge-healer -c healer --timestamps > {log_file}"
        )

        # # wait for restore
        # while True:
        #     current = get_restore_count()
        #     if current is not None and current > pre_count:
        #         break
        #     time.sleep(RETRY_INTERVAL)

        # latency = time.perf_counter() - start
        # latencies.append(latency)
        # log.info(f"Pod {pod} restored in {latency:.3f}s")
        # time.sleep(PAUSE_SECONDS)

    latencies.sort()
    def pct(p): return latencies[int(p * len(latencies))]
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
