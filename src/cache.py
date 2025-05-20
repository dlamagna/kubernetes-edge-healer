"""SQLite helper to persist desired ReplicaSet specs for cold‑boot."""
import json
import aiosqlite

logger = logging.getLogger("edge-healer.cache")

class DesiredStateCache:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        import os, pathlib
        pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # open (and create) the DB file
        async with aiosqlite.connect(self.path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS rs (uid TEXT PRIMARY KEY, spec TEXT)")
            await db.commit()

    async def save_rs(self, spec_dict, uid, verbose=True):
        """
        Persist only the spec (plain JSON) of the ReplicaSet.
        """
        if verbose:
            logger.debug("save_rs: uid=%r, spec_dict type=%s", uid, type(spec_dict))
            # Optionally show a small preview (if huge, truncate):
            preview = repr(spec_dict)
            if len(preview) > 200:
                preview = preview[:200] + "…"
            logger.debug("save_rs: spec_dict preview=%s", preview)

        try:
            spec_str = json.dumps(spec_dict)
        except TypeError as e:
            # Log the exact error and the offending object
            logger.error("json.dumps failed: %s; object repr=%r", e, spec_dict)
            raise

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "REPLACE INTO rs(uid, spec) VALUES(?, ?)",
                (uid, spec_str)
            )
            await db.commit()
        
        if verbose:
            logger.debug("save_rs: successfully wrote uid=%r", uid)

    async def load_all(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT spec FROM rs") as cursor:
                return [json.loads(row[0]) async for row in cursor]
