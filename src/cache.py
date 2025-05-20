"""SQLite helper to persist desired ReplicaSet specs for cold-boot."""
import json
import aiosqlite
import logging
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
        Save a ReplicaSet object (Kopf Body, Kubernetes model, or dict) into SQLite.
        If `self.verbose` is True, emit detailed debug info.
        """
        # Extract UID
        if hasattr(rs_obj, "metadata") and hasattr(rs_obj.metadata, "uid"):
            uid = rs_obj.metadata.uid
        else:
            uid = rs_obj.get("metadata", {}).get("uid")

        # Convert object to dict via duck-typing or to_dict
        try:
            if hasattr(rs_obj, "items") and hasattr(rs_obj, "get"):
                # Kopf Body and other mapping-like objects
                data = dict(rs_obj)
            elif hasattr(rs_obj, "to_dict"):
                data = rs_obj.to_dict()
            else:
                msg = f"Unexpected ReplicaSet object type: {type(rs_obj)!r}"
                cache_logger.error(msg)
                raise ValueError(msg)
        except Exception as exc:
            cache_logger.error("Failed to convert rs_obj to dict: %r; object=%r", exc, rs_obj)
            raise ValueError(f"Failed to convert ReplicaSet object to dict: {exc!r}; object was: {rs_obj!r}")

        # JSON-serialize the dict
        try:
            spec_str = json.dumps(data)
        except (TypeError, ValueError) as exc:
            cache_logger.error("json.dumps failed for uid=%r: %r; data repr: %r", uid, exc, data)
            raise ValueError(f"Failed to JSON-serialize data for uid {uid}: {exc!r}")

        if self.verbose:
            preview = repr(data)
            if len(preview) > 300:
                preview = preview[:300] + "…"
            cache_logger.debug(
                "save_rs: uid=%r, data type=%s, preview=%s",
                uid, type(data), preview,
            )

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
                rows = await cursor.fetchall()
                return [json.loads(row[0]) for row in rows]
