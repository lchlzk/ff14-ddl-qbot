"""Periodic cleanup outside message handlers; no public-image expiration."""
from __future__ import annotations

import asyncio
import shutil
import time

from .storage import Store


def run(store: Store, now: float | None = None) -> None:
    from .ai_store import AIStore
    from .learning_chat import LearningStore, SEEN_TTL, CONTEXT_TTL

    now = time.time() if now is None else now
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        AIStore._purge(db, now)
        db.execute("DELETE FROM rates WHERE last<?", (now-86400,))
        db.execute("DELETE FROM learning_seen WHERE created<?", (now-SEEN_TTL,))
        db.execute("DELETE FROM learning_repeat_state WHERE updated<?", (now-CONTEXT_TTL,))
        scopes = [row[0] for row in db.execute("SELECT DISTINCT scope FROM learning_contents")]
    # Separate transactions let ordinary messages write between groups.
    for scope in scopes:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            LearningStore._cleanup(db, scope, now)
    previous = store.document("system:integrity")
    if now - previous.get("checked", 0) >= 86400:
        with store.connect() as db:
            healthy = all(row[0] == "ok" for row in db.execute("PRAGMA quick_check"))
        with store.state("system:integrity") as doc:
            doc.update(ok=healthy, checked=now)
    space = shutil.disk_usage(store.path)
    with store.state("system:disk") as doc:
        doc.update(free=space.free, total=space.total, checked=now,
                   warning=space.free < max(512 * 1024**2, space.total * 0.05))


async def worker(store: Store) -> None:
    from nonebot.log import logger
    from .gallery import migrate_legacy
    while True:
        try:
            await asyncio.to_thread(run, store)
            await asyncio.to_thread(migrate_legacy, store)
        except Exception as exc:
            logger.warning("Database maintenance failed ({})", type(exc).__name__)
        await asyncio.sleep(60)
