import requests

METRICS_URL = "http://172.20.0.4:8000/metrics"  
METRIC_NAME = "restore_latency_seconds_count"

try:
    r = requests.get(METRICS_URL, timeout=3)
    r.raise_for_status()
except Exception as e:
    print(f"Failed to fetch metrics: {e}")
    exit(1)

lines = r.text.splitlines()
matches = [l for l in lines if l.startswith(METRIC_NAME)]
if matches:
    print(f"Found {len(matches)} line(s) for '{METRIC_NAME}':")
    for l in matches:
        print("   " + l)
else:
    print(f"Metric '{METRIC_NAME}' not found. Available metrics:")
    # print first 10 for context
    for l in lines[:10]:
        print("   " + l)
