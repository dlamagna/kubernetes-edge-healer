"""SQLite helper to persist desired ReplicaSet specs for cold‑boot."""
import json
import aiosqlite

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

    async def save_rs(self, rs_obj):

        uid = rs_obj["metadata"]["uid"]
        # only save the spec object
        rs_spec = rs_obj.get("spec", {})
        spec_str = json.dumps(rs_spec)

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "REPLACE INTO rs(uid, spec) VALUES(?, ?)",
                (uid, spec_str)
            )
            await db.commit()

    async def load_all(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT spec FROM rs") as cursor:
                return [json.loads(row[0]) async for row in cursor]
