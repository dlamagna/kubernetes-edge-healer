"""Entrypoint for the edge‑healer operator."""
import asyncio
import logging
import os
import signal
from contextlib import suppress

import kopf
from kubernetes_asyncio import client, config

from gossip import SerfGossip
from metrics import start_metrics_server, RESTORE_LATENCY, BIND_CONFLICTS, PEER_UPDATES
from scheduler import bid_and_bind
from cache import DesiredStateCache

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("edge-healer")

NODE_NAME = os.getenv("NODE_NAME") or os.uname().nodename
GOSSIP_ADDR = os.getenv("GOSSIP_ADDR", "127.0.0.1:7373")  # serf side‑car RPC
CACHE_PATH = os.getenv("CACHE_PATH", "/data/desired.db")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))


@kopf.on.startup()
async def startup(**_):
    """Initialise global resources (K8s client, gossip, cache, metrics)."""
    # 1. K8s client
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        # sync call, not a coroutine:
        config.load_incluster_config()
    else:
        # async loader for out-of-cluster dev:
        await config.load_kube_config()
    api = client.CoreV1Api()

    # 2. Desired‑state cache
    cache = DesiredStateCache(CACHE_PATH)
    await cache.init()

    # 3. Serf gossip client (runs forever in background)
    gossip = SerfGossip(NODE_NAME, GOSSIP_ADDR, peer_update_counter=PEER_UPDATES)
    asyncio.create_task(gossip.run())

    # 4. Metrics server
    start_metrics_server(METRICS_PORT)
    logger.info("Prometheus metrics at :%d/metrics", METRICS_PORT)

    global API, GOSSIP, CACHE
    API = api
    GOSSIP = gossip
    CACHE = cache


    # 5. Register globals for handlers
    # kopf.register_global("api", api)
    # kopf.register_global("gossip", gossip)
    # kopf.register_global("cache", cache)

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.scanning.namespaces = ["default"]


# -----------------------------------------------------------------------------
# Pod event handler – trigger bidding when a local Replica is lost
# -----------------------------------------------------------------------------
@kopf.on.resume("", "v1", "pods", when=lambda meta, **_: meta.get("spec", {}).get("nodeName") == NODE_NAME)
@kopf.on.delete("", "v1", "pods", when=lambda meta, **_: meta.get("spec", {}).get("nodeName") == NODE_NAME)
async def on_pod_gone(meta, namespace, name, body, patch, **kwargs):
    """Called when a Pod we hosted disappears => try to restore it."""
    # use module-level globals
    # API and GOSSIP were set in startup()
    start_ts = asyncio.get_event_loop().time()
    try:
        await bid_and_bind(API, GOSSIP, meta, namespace, name)
        # Observe latency only on success
        logger.info(f"[METRICS-CANARY] About to observe latency: {asyncio.get_event_loop().time() - start_ts:.3f}s")
        RESTORE_LATENCY.observe(asyncio.get_event_loop().time() - start_ts)
        

    except bid_and_bind.BindConflict:
        BIND_CONFLICTS.inc()
    except Exception as exc:
        logger.error("restore failed: %s", exc)


# -----------------------------------------------------------------------------
# ReplicaSet event handler – keep desired spec in SQLite cache
# -----------------------------------------------------------------------------
@kopf.on.update("apps", "v1", "replicasets")
@kopf.on.create("apps", "v1", "replicasets")
async def on_rs_change(spec, meta, body, **_):
    # cache: DesiredStateCache = kopf.get_global("cache")
    # await cache.save_rs(body)
    # use module-level CACHE
    await CACHE.save_rs(body)

# -----------------------------------------------------------------------------
# graceful exit helpers -------------------------------------------------------
# -----------------------------------------------------------------------------
async def _shutdown(loop):
    logger.info("shutdown requested – cancelling tasks…")
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
    [task.cancel() for task in tasks]
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)

for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda *_: asyncio.create_task(_shutdown(asyncio.get_event_loop())))

# kopf will now run until process is stopped ---------------------------------------------------
