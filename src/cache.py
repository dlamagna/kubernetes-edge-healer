"""SQLite helper to persist desired ReplicaSet specs for cold‑boot."""
import json
import aiosqlite
import logging
import copy
from typing import MutableMapping

cache_logger = logging.getLogger("edge-healer.cache")
cache_logger.setLevel(logging.DEBUG)

class DesiredStateCache:
    def __init__(self, path, *, verbose=True):
        self.path = path
        self.verbose = verbose

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS rs (uid TEXT PRIMARY KEY, spec TEXT)"
            )
            await db.commit()

    async def save_rs(self, rs_obj):
        """
        Save a ReplicaSet object (Kopf Body or dict) into SQLite.
        If `self.verbose` is True, emit detailed debug info.
        """
        # Extract UID
        uid = None
        if hasattr(rs_obj, "metadata"):
            uid = rs_obj.metadata.uid
        else:
            uid = rs_obj.get("metadata", {}).get("uid")

        # Convert any Kubernetes model into plain dict
        try:
            if hasattr(rs_obj, "to_dict"):
                data = rs_obj.to_dict()
            # if it’s Kopf’s Body, it behaves like a dict already:
            elif isinstance(rs_obj, MutableMapping):
                data = dict(rs_obj)
            else:
                cache_logger.error("Unexpected body type %r", type(rs_obj))
                raise
            spec_str = json.dumps(data)
            
        except Exception as e:
            cache_logger.error("Failed to convert rs_obj to dict: %s (%r)", e, rs_obj)
            raise ValueError("Failed to convert rs_obj to dict: %s (%r)", e, rs_obj)

        if self.verbose:
            preview = repr(data)
            if len(preview) > 300:
                preview = preview[:300] + "…"
            cache_logger.debug("save_rs: uid=%r, data type=%s, preview=%s",
                         uid, type(data), preview)

        # JSON-serialize
        try:
            spec_str = json.dumps(data)
        except TypeError as e:
            cache_logger.error("json.dumps failed on uid=%r: %s; repr(data)=%r",
                         uid, e, data)
            raise

        # Persist to SQLite
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "REPLACE INTO rs(uid, spec) VALUES(?, ?)",
                (uid, spec_str)
            )
            await db.commit()
        if self.verbose:
            cache_logger.debug("save_rs: successfully wrote uid=%r", uid)

    async def load_all(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT spec FROM rs") as cursor:
                return [json.loads(row[0]) async for row in cursor]
