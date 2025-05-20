"""Bidding & optimistic CAS‑binding logic."""
import asyncio
import logging
from typing import Dict

from kubernetes_asyncio import client
from kubernetes_asyncio.client.rest import ApiException

logger = logging.getLogger("scheduler")

class BindConflict(Exception):
    """Raised when another node won the race."""

async def bid_and_bind(api: client.CoreV1Api, gossip, pod_meta, namespace: str, name: str):
    peers: Dict[str, int] = gossip.healthy_peers()
    my_cpu = peers.get(gossip.node, 0)
    if any(cpu > my_cpu for cpu in peers.values()):
        logger.debug("lost bid for %s/%s", namespace, name)
        return  # lost bid

    # Try optimistic `/binding` sub‑resource
    target = client.V1Binding(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        target=client.V1ObjectReference(kind="Node", api_version="v1", name=gossip.node),
    )
    try:
        await api.create_namespaced_pod_binding(name, namespace, target, _preload_content=False)
        logger.info("won bid – bound pod %s/%s to %s", namespace, name, gossip.node)
    except ApiException as e:
        if e.status == 409:
            raise BindConflict from e
        raise