"""Entrypoint for the edge-healer operator."""
import asyncio
import logging
import os
import signal
from contextlib import suppress

import kopf
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException

from gossip import SerfGossip
from metrics import start_metrics_server, RESTORE_LATENCY, BIND_CONFLICTS, PEER_UPDATES
from scheduler import bid_and_bind, BindConflict
from cache import DesiredStateCache

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
logging.basicConfig(level=LOG_LEVEL)
logging.getLogger("edge-healer.cache").setLevel(logging.DEBUG)
logger = logging.getLogger("edge-healer")

NODE_NAME = os.getenv("NODE_NAME") or os.uname().nodename
GOSSIP_ADDR = os.getenv("GOSSIP_ADDR", "127.0.0.1:7373")  # Serf side-car RPC
CACHE_PATH = os.getenv("CACHE_PATH", "/data/desired.db")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))

# these will be populated on startup():
API: client.CoreV1Api
GOSSIP: SerfGossip
CACHE: DesiredStateCache


# -----------------------------------------------------------------------------
# Startup: load kube config, spin up gossip, cache, metrics
# -----------------------------------------------------------------------------
@kopf.on.startup()
async def startup(**_):
    # 1. Kubernetes client
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        await config.load_kube_config()
    api = client.CoreV1Api()

    # 2. Desired-state cache
    cache = DesiredStateCache(CACHE_PATH)
    await cache.init()

    # 3. Serf gossip (runs in background)
    gossip = SerfGossip(NODE_NAME, GOSSIP_ADDR, peer_update_counter=PEER_UPDATES)
    asyncio.create_task(gossip.run())

    # 4. Metrics server
    start_metrics_server(METRICS_PORT)
    logger.info("Prometheus metrics at :%d/metrics", METRICS_PORT)

    # expose to handlers
    global API, GOSSIP, CACHE
    API = api
    GOSSIP = gossip
    CACHE = cache


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.scanning.namespaces = ["default"]


# -----------------------------------------------------------------------------
# Helper to detect control-plane outage
# -----------------------------------------------------------------------------
async def is_offline(timeout: float = 1.0) -> bool:
    """
    Return True if the Kubernetes API server is unreachable.
    """
    try:
        logger.debug("Checking API reachability via get_api_resources()")
        await asyncio.wait_for(API.get_api_resources(), timeout)
        logger.debug("  control-plane reachable")
        return False
    except (asyncio.TimeoutError, ApiException, OSError) as e:
        logger.debug("Control-plane check failed (assuming offline): %s", e)
        return True


# -----------------------------------------------------------------------------
# Pod event handler – trigger bidding when a local Pod disappears offline
# -----------------------------------------------------------------------------
@kopf.on.resume("", "v1", "pods")
@kopf.on.delete("", "v1", "pods")
async def on_pod_gone(meta, namespace, name, **kwargs):
    """
    Called when a Pod we hosted disappears on THIS node.
    Only attempt restore if the control-plane is unreachable (i.e. offline).
    """
    logger.debug("POD GONE event fired for %s/%s; meta=%r", namespace, name, meta)
    offline = await is_offline()
    logger.debug("  is_offline() → %s", offline)
    if not offline:
        logger.debug("  skipping because we're online")
        return

    logger.info("Offline Pod loss detected: %s/%s → bidding…", namespace, name)
    start_ts = asyncio.get_event_loop().time()

    try:
        await bid_and_bind(API, GOSSIP, meta, namespace, name)
        logger.info("bid_and_bind() returned successfully for %s/%s", namespace, name)
        latency = asyncio.get_event_loop().time() - start_ts
        RESTORE_LATENCY.observe(latency)
        logger.info("Restored %s/%s in %.3fs", namespace, name, latency)

    except BindConflict:
        BIND_CONFLICTS.inc()
        logger.debug("Lost bid for %s/%s", namespace, name)

    except Exception as exc:
        logger.error("Error during offline restore of %s/%s: %s", namespace, name, exc)


# -----------------------------------------------------------------------------
# ReplicaSet event handler – keep desired spec in SQLite cache
# -----------------------------------------------------------------------------
@kopf.on.update("apps", "v1", "replicasets")
@kopf.on.create("apps", "v1", "replicasets")
async def on_rs_change(body, **_):
    await CACHE.save_rs(body)


# -----------------------------------------------------------------------------
# graceful shutdown
# -----------------------------------------------------------------------------
async def _shutdown(loop):
    logger.info("shutdown requested – cancelling tasks…")
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
    for task in tasks:
        task.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)

for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda *_: asyncio.create_task(_shutdown(asyncio.get_event_loop())))


# @kopf.on.delete("apps", "v1", "replicasets")
# async def on_rs_delete(body, **_):
#     uid = body.get("metadata", {}).get("uid")
#     cache_logger.debug("Deleting RS from cache: uid=%r", uid)
#     async with aiosqlite.connect(CACHE_PATH) as db:
#         await db.execute("DELETE FROM rs WHERE uid = ?", (uid,))
#         await db.commit()
#     cache_logger.debug("Deleted RS uid=%r from cache", uid)
