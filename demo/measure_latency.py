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
# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# File handler (always DEBUG level)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

# Console handler (INFO level by default, DEBUG if verbose)
console_handler = logging.StreamHandler(sys.stdout)
console_level = logging.DEBUG if args.verbose else logging.INFO
console_handler.setLevel(console_level)
console_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
console_handler.setFormatter(console_formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
log = logging.getLogger("measure_latency")

# ________________________

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
    log.debug("API server blocked")

def unblock_api():
    log.info("Unblocking API server")
    run_cmd(
        f"sudo iptables -D OUTPUT "
        f"-p tcp --dport {API_PORT} "
        f"-d {API_SERVER} "
        f"-j DROP"
    )
    log.debug("API server unblocked")
def ensure_nft_base():
    # Create table & chain if they don't exist
    subprocess.call("nft list table inet filter >/dev/null 2>&1 || sudo nft add table inet filter", shell=True)
    subprocess.call(
        "nft list chain inet filter output >/dev/null 2>&1 || "
        "sudo nft add chain inet filter output '{ type filter hook output priority 0; policy accept; }'",
        shell=True
    )

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

    lines = resp.text.splitlines()
    for line in lines:
        if line.startswith("restore_latency_seconds_count"):
            count = int(float(line.split()[-1]))
            log.debug(f"Current restore count = {count}")
            return count

    log.error("Metric 'restore_latency_seconds_count' not found! Full payload below:")
    for line in lines[:50]:
        log.error("  %s", line)
    return None

def get_pod_name(timeout=30.0, interval=0.5):
    """
    Poll until a Pod appears, logging each attempt.
    """
    log.info(f"Waiting up to {timeout}s for a Pod with label '{LABEL_SELECTOR}'")
    deadline = time.time() + timeout
    attempt = 1
    while True:
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

        if time.time() > deadline:
            log.error(f"No Pod matching '{LABEL_SELECTOR}' after {attempt} attempts")
            raise RuntimeError(
                f"No Pod matching '{LABEL_SELECTOR}' appeared within {timeout:.1f}s"
            )
        attempt += 1
        time.sleep(interval)

def measure_restore_latency():
    latencies = []
    for i in range(1, ITERATIONS + 1):
        log.info(f"=== Iteration {i}/{ITERATIONS} ===")
        pod = get_pod_name()
        pre_count = get_restore_count()
        if pre_count is None:
            log.error("Cannot read initial restore count — aborting test")
            sys.exit(1)

        # 1) Start network outage
        log.info("Step 1: simulate WAN outage")
        block_api()
        log.debug(f"Sleeping for OUTAGE_DURATION = {OUTAGE_DURATION}s")
        time.sleep(OUTAGE_DURATION)

        # 2) Delete the Pod while control-plane is down
        log.info(f"Step 2: deleting Pod {pod}")
        start = time.perf_counter()
        run_cmd(f"kubectl delete pod {pod} -n {BUSYBOX_NAMESPACE} --grace-period=0 --force")

        # 3) Heal network
        log.info("Step 3: healing WAN")
        unblock_api()

        # 4) Wait until Prometheus counter increments
        log.info("Step 4: waiting for restore counter to increment")
        while True:
            current = get_restore_count()
            if current > pre_count:
                break
            log.debug(f"restore count {current} ≤ {pre_count}; sleeping 10ms")
            time.sleep(0.01)

        latency = time.perf_counter() - start
        latencies.append(latency)
        log.info(f"Pod {pod} restored in {latency:.3f}s")
        log.debug(f"Sleeping for PAUSE_SECONDS = {PAUSE_SECONDS}s before next iteration")
        time.sleep(PAUSE_SECONDS)

    # Summary
    latencies.sort()
    def pct(p): return latencies[int(p * len(latencies))]
    log.info("=== Summary ===")
    log.info(f"  p50 = {pct(0.50):.3f}s")
    log.info(f"  p90 = {pct(0.90):.3f}s")
    log.info(f"  p99 = {pct(0.99):.3f}s")


if __name__ == "__main__":
    # Ensure we clean up if the script is interrupted
    def cleanup(signum, frame):
        log.warning("Interrupted! Cleaning up and exiting.")
        try:
            unblock_api()
        except Exception:
            pass
        sys.exit(1)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    ensure_nft_base()
    measure_restore_latency()
