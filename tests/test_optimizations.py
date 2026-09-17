from __future__ import annotations

import asyncio
import hashlib
import io
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from bot_tools import community, gallery
from bot_tools.ai_queue import FairLimiter
from bot_tools.ai_store import AIStore
from bot_tools.learning_chat import LearningStore
from bot_tools.media_objects import LocalObjects
from bot_tools.request_cache import ResponseCache, singleflight
from bot_tools.storage import Identity, Store, ToolError
from bot_tools.web_admin import WebAdmin
from tools.prune_runtime import prune
from tools.pack_release import FILES, pack


class TracedStore(Store):
    def __init__(self, path):
        self.sql = []
        super().__init__(path)

    @contextmanager
    def connect(self):
        with super().connect() as db:
            db.set_trace_callback(self.sql.append)
            yield db


class OptimizedDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TracedStore(self.temp.name)
        self.admin = WebAdmin(self.store)
        self.who = Identity("test", "group:optimization", "user", group_role="owner")

    def test_chinese_username_can_actually_log_in(self):
        self.admin.set_account("中文管理员", "Synthetic-Pass42")
        token, _ = self.admin.authenticate("中文管理员", "Synthetic-Pass42", "test-client")
        self.assertIsNotNone(self.admin.session(token))

    def test_overlong_password_cannot_match_a_substitute_string(self):
        self.admin.set_account("admin", "invalid-too-long")
        with self.assertRaises(ToolError):
            self.admin.authenticate("admin", "a" * 129, "test-client")

    def test_hashing_releases_db_writer_and_reset_cannot_race_login(self):
        self.admin.set_account("admin", "Synthetic-Pass42")
        entered, resume = threading.Event(), threading.Event()
        from bot_tools.web_admin import _password_hash

        def slow_hash(*args):
            entered.set()
            if not resume.wait(5):
                raise RuntimeError("test hashing gate timed out")
            return _password_hash(*args)

        with ThreadPoolExecutor(1) as pool, patch("bot_tools.web_admin._password_hash", side_effect=slow_hash):
            future = pool.submit(self.admin.authenticate, "admin", "Synthetic-Pass42", "test-client")
            try:
                self.assertTrue(entered.wait(3))
                with self.store.connect() as db:
                    db.execute("PRAGMA busy_timeout=100")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE web_accounts SET password_hash=?", ("0" * 64,))
            finally:
                resume.set()
            with self.assertRaisesRegex(ToolError, "已更新"):
                future.result(3)

    def test_idle_message_paths_are_read_only_and_small(self):
        ai = AIStore(self.store)
        learned = LearningStore(self.store)
        self.store.sql.clear()
        self.assertEqual(ai.collect_targets(self.who, "普通消息", "id"), [])
        community.keyword_lookup(self.store, self.who, "没有这个关键词")
        community.gate(self.store, self.who, "learn")
        learned.config(self.who)
        self.assertFalse(any(q.startswith(("BEGIN", "DELETE", "UPDATE", "INSERT")) for q in self.store.sql))
        self.assertLessEqual(len(self.store.sql), 5)

    def test_overview_does_not_run_integrity_scan(self):
        self.store.sql.clear()
        self.admin.overview()
        self.assertFalse(any("quick_check" in q.lower() for q in self.store.sql))

    def test_groups_are_sql_paged_before_loading_their_stats(self):
        with self.store.connect() as db:
            db.executemany("INSERT INTO group_metadata(scope,manual_name) VALUES(?,?)",
                           [(hashlib.sha256(str(i).encode()).hexdigest(), f"测试群{i}") for i in range(90)])
        self.store.sql.clear()
        page = self.admin.groups(2, "测试群")
        self.assertEqual(page["total"], 90)
        self.assertEqual(len(page["items"]), 24)
        self.assertTrue(any("LIMIT 24 OFFSET 24" in q for q in self.store.sql))
        self.assertFalse(any("SELECT key,value FROM documents" in q for q in self.store.sql))

    def test_hot_queries_have_usable_indexes(self):
        with self.store.connect() as db:
            for query, args in [
                ("SELECT id FROM learning_pairs WHERE prompt_key=? AND banned=0 AND count>=?", ("test", 1)),
                ("DELETE FROM ai_runs WHERE created<?", (0,)),
                ("SELECT COUNT(*) FROM ai_groups WHERE owner=?", ("test",)),
            ]:
                plan = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + query, args))
                self.assertNotIn("SCAN", plan)

    def test_media_file_deduplicates_and_local_gallery_stays_isolated(self):
        image = b"test-object-bytes"
        first = gallery.add(self.store, self.who, "one", image)
        gallery.add(self.store, self.who, "two", image)
        with self.store.connect() as db:
            rows = list(db.execute("SELECT image,object_key,byte_size,visibility FROM gallery"))
        self.assertEqual(rows[0]["object_key"], rows[1]["object_key"])
        self.assertEqual(rows[0]["image"], b"")
        self.assertEqual(rows[0]["visibility"], "public")
        self.assertEqual(gallery.get(self.store, self.who, "one"), image)
        self.assertEqual(gallery.stats(self.store, self.who)[2], len(image)*2)
        self.admin.delete_gallery(first)
        self.assertEqual(gallery.get(self.store, self.who, "two"), image)

    def test_legacy_migration_is_backed_up_verified_and_resumable(self):
        image = b"legacy-raw-GIF-bytes"
        with self.store.connect() as db:
            db.execute("INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                       (self.who.scope_key, "gif", "old", image))
        self.assertEqual(gallery.migrate_legacy(self.store), 1)
        self.assertEqual(gallery.migrate_legacy(self.store), 0)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM gallery").fetchone()
        self.assertEqual(gallery.read_row(self.store, row), image)
        self.assertEqual(row["image"], b"")
        backup = next((self.store.path / "backups").glob("before-gallery-files-*.sqlite3"))
        connection = sqlite3.connect(backup)
        try:
            self.assertEqual(connection.execute("SELECT image FROM gallery").fetchone()[0], image)
        finally:
            connection.close()

    def test_failed_object_write_leaves_legacy_blob_untouched(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                       (self.who.scope_key, "gif", "old", b"original"))
        with patch.object(LocalObjects, "put", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                gallery.migrate_legacy(self.store)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT image FROM gallery").fetchone()[0], b"original")

    def test_gallery_rollback_restores_original_and_pauses_migration(self):
        gallery.add(self.store, self.who, "first", b"first-original")
        self.assertEqual(gallery.restore_blobs(self.store), 1)
        gallery.add(self.store, self.who, "second", b"second-original")
        self.assertEqual(gallery.migrate_legacy(self.store), 0)
        with self.store.connect() as db:
            rows = list(db.execute("SELECT image,object_key FROM gallery ORDER BY id"))
        self.assertEqual([r["image"] for r in rows], [b"first-original", b"second-original"])
        self.assertTrue(all(not r["object_key"] for r in rows))
        self.assertEqual(gallery.get(self.store, self.who, "first"), b"first-original")

    def test_migration_rechecks_pause_under_lock(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                       (self.who.scope_key, "gif", "old", b"original"))
        real_backend = LocalObjects

        def paused_backend(path):
            with self.store.state("gallery:storage") as doc:
                doc["mode"] = "sqlite"
            return real_backend(path)

        with patch("bot_tools.gallery.LocalObjects", side_effect=paused_backend):
            self.assertEqual(gallery.migrate_legacy(self.store), 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT image FROM gallery").fetchone()[0], b"original")

    def test_package_uses_allowlist_and_rejects_real_env(self):
        import zipfile
        root = self.store.path / "release"
        root.mkdir()
        for name in FILES:
            (root / name).write_bytes(b"synthetic release fixture")
        (root / "unlisted-secret.txt").write_text("must not be distributed")
        archive_path = pack(root)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), set(FILES))
            self.assertIsNone(archive.testzip())
        (root / ".env").write_text("synthetic forbidden file")
        with self.assertRaisesRegex(ValueError, "real .env"):
            pack(root)

    def test_thumbnail_does_not_change_animated_original(self):
        out = io.BytesIO()
        frames = [Image.new("RGB", (600, 600), color) for color in ("red", "blue")]
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
        original = out.getvalue()
        item = gallery.add(self.store, self.who, "gif", original)
        thumb = self.admin.gallery_thumbnail(item)
        with Image.open(io.BytesIO(thumb)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertLessEqual(max(im.size), 360)
        self.assertEqual(self.admin.gallery_image(item)[0], original)

    def test_dependency_pruning_preserves_runtime_and_licenses(self):
        root = self.store.path / "deps"
        for name in ("numpy/_core/tests/sample.py", "numpy/testing/README.txt", "matplotlib/mpl-data/fonts/font.ttf", "numpy/LICENSE.txt"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.assertGreater(prune(root), 0)
        self.assertFalse((root / "numpy/_core/tests").exists())
        self.assertTrue((root / "numpy/testing/README.txt").exists())
        self.assertTrue((root / "matplotlib/mpl-data/fonts/font.ttf").exists())
        self.assertTrue((root / "numpy/LICENSE.txt").exists())


class OptimizedAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_singleflight_coalesces_and_cancellation_does_not_cancel_others(self):
        release = asyncio.Event()
        calls = 0

        @singleflight(lambda value: value)
        async def read(value):
            nonlocal calls
            calls += 1
            await release.wait()
            return value

        first = asyncio.create_task(read("same"))
        second = asyncio.create_task(read("same"))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await second, "same")
        self.assertEqual(calls, 1)

    async def test_ai_queue_is_bounded_and_other_groups_can_progress(self):
        limiter = FairLimiter(maximum=2, per_group=1, pending=1, group_pending=1, wait_seconds=0.03)
        async with limiter.slot("busy"):
            async def wait():
                async with limiter.slot("busy"):
                    self.fail("busy group must still be blocked")
            queued = asyncio.create_task(wait())
            await asyncio.sleep(0)
            async with limiter.slot("other-group"):
                pass  # A busy group's waiting queue must not monopolize free slots.
            with self.assertRaisesRegex(ToolError, "队列已满"):
                async with limiter.slot("busy"):
                    pass
            with self.assertRaisesRegex(ToolError, "排队"):
                await queued
            async with limiter.slot("other-group"):
                pass
        async with limiter.slot("busy"):
            pass  # No leaked slot after timeout.

    async def test_ai_cancelled_waiter_releases_queue_slot(self):
        limiter = FairLimiter(maximum=1, per_group=1)
        async with limiter.slot("one"):
            async def wait():
                async with limiter.slot("two"):
                    pass
            queued = asyncio.create_task(wait())
            await asyncio.sleep(0)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
        async with limiter.slot("two"):
            pass

    def test_cache_budget_is_memory_bounded_and_clear_resets_accounting(self):
        cache = ResponseCache(max_bytes=2000, max_entries=3)
        for i in range(10):
            cache[str(i)] = (i, {"text": "x" * 300})
        self.assertLessEqual(cache.bytes, 2000)
        self.assertLessEqual(len(cache), 3)
        cache["huge"] = {"text": "x" * 9000}
        self.assertNotIn("huge", cache)
        cache.clear()
        self.assertEqual(cache.bytes, 0)
