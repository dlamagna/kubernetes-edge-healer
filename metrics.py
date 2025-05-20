"""Prometheus metrics + HTTP server."""
import logging
from threading import Thread

from prometheus_client import Counter, Histogram, start_http_server

RESTORE_LATENCY = Histogram(
    "restore_latency_seconds",
    "End‑to‑end Pod restore latency seconds",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5),
)
BIND_CONFLICTS = Counter("bind_conflicts_total", "Number of /bind CAS conflicts")
PEER_UPDATES = Counter("peer_updates_total", "Peer gossip update messages processed")


def start_metrics_server(port: int = 8000):
    logging.getLogger("metrics").info("starting Prometheus HTTP server on :%d", port)
    Thread(target=start_http_server, args=(port,), daemon=True).start()