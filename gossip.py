"""Async wrapper around python‑serfclient for free‑CPU gossip."""
import json
import socket
import struct
import time
from asyncio import sleep
from collections import defaultdict
from contextlib import suppress
from typing import Dict

from prometheus_client import Counter
from serfclient import SerfClient

class SerfGossip:
    def __init__(self, node_name: str, addr: str, *, peer_update_counter: Counter):
        self.node = node_name
        host, port = addr.split(":")
        self.addr = (host, int(port))
        self.peers: Dict[str, int] = defaultdict(int)
        self._updates = peer_update_counter

    async def run(self):
        while True:
            try:
                with SerfClient(host=self.addr[0], port=self.addr[1]) as serf:
                    async for event in self._events(serf):
                        if event.name == "query" and event.payload:
                            data = json.loads(event.payload)
                            self.peers[event.src] = data.get("free_cpu", 0)
                            self._updates.inc()
            except Exception as exc:
                logging.getLogger("gossip").warning("gossip loop error: %s", exc)
                await sleep(2.0)

    async def _events(self, serf):
        # Blocking generator adapter to async
        while True:
            ev = serf.event()
            yield ev
            await sleep(0)  # shift control back to loop

    async def broadcast_free_cpu(self, milli: int):
        with suppress(Exception):
            with SerfClient(host=self.addr[0], port=self.addr[1]) as serf:
                serf.event("free_cpu", json.dumps({"free_cpu": milli}), coalesce=True)

    def healthy_peers(self) -> Dict[str, int]:
        # Simple copy; in practice add TTL filtering
        return dict(self.peers)
