from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from bot_tools import community, gallery, media
from bot_tools.storage import Identity, Store, ToolError


def png(color="red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(output, "PNG")
    return output.getvalue()


def gif() -> bytes:
    output = io.BytesIO()
    frames = [Image.new("RGBA", (20, 20), color) for color in ("red", "green", "blue")]
    frames[0].save(output, "GIF", save_all=True, append_images=frames[1:],
                   duration=[80, 160, 240], loop=3, disposal=2, comment=b"original")
    return output.getvalue()


class GalleryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)
        self.a = Identity("app", "group:a", "a", group_role="owner")
        self.b = Identity("app", "group:b", "b", group_role="owner")
        self.member = replace(self.a, user="member", group_role="member")
        self.private = Identity("app", "private:root", "root", True)

    def select(self, who, mode):
        return community.management(self.store, who, "group", "gallery " + mode)

    def authorize_root(self):
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        self.store.grant(record["code"], "owner")

    def test_groups_default_public_private_is_always_public_and_explicit_group_local_is_kept(self):
        self.assertTrue(gallery.current(self.store, self.a).public)
        self.assertTrue(gallery.current(self.store, self.b).public)
        self.assertTrue(gallery.current(self.store, self.private).public)
        self.select(self.a, "local")
        self.assertFalse(gallery.current(self.store, self.a).public)

    def test_only_current_group_owner_or_server_root_can_switch(self):
        denied = [self.member, replace(self.a, group_role="admin"), self.private,
                  replace(self.a, scope="channel:a"), replace(self.a, group_role=None)]
        for who in denied:
            with self.subTest(who=who), self.assertRaises(ToolError):
                self.select(who, "public")
        self.select(self.a, "公共")
        self.assertTrue(gallery.current(self.store, self.a).public)
        # The group owner role must not survive a subsequent QQ role change.
        with self.assertRaises(ToolError):
            self.select(replace(self.a, group_role="admin"), "local")
        self.select(self.a, "本群")
        self.assertFalse(gallery.current(self.store, self.a).public)
        self.authorize_root()
        with self.assertRaises(ToolError):
            self.select(self.private, "local")
        self.assertTrue(gallery.current(self.store, self.private).public)

    def test_switch_retains_old_images_without_publishing_or_copying_them(self):
        self.select(self.a, "local")
        first = media.gallery_add(self.store, self.member, "cat", png())
        self.assertEqual(first, media.gallery_add(self.store, self.member, "cat", png()))
        self.select(self.a, "public")
        self.select(self.b, "public")
        self.assertIsNone(media.gallery_get(self.store, self.a, "cat"))
        self.assertIsNone(media.gallery_get(self.store, self.b, "cat"))
        second = media.gallery_add(self.store, self.member, "cat", png("blue"))
        self.assertNotEqual(first, second)
        self.assertEqual(media.gallery_get(self.store, self.a, "cat"), media.gallery_get(self.store, self.b, "cat"))
        self.select(self.a, "local")
        self.assertEqual(media.gallery_get(self.store, self.a, "cat"), media.checked_image(png()))
        self.assertEqual(media.gallery_get(self.store, self.b, "cat"), media.checked_image(png("blue")))
        self.select(self.a, "public")
        self.assertEqual(gallery.stats(self.store, self.a)[1], 1)

    def test_default_public_storage_is_shared_with_private_and_other_bots(self):
        media.gallery_add(self.store, self.member, "cat", png())
        self.assertIsNotNone(media.gallery_get(self.store, self.b, "cat"))
        self.assertIsNotNone(media.gallery_get(self.store, self.private, "cat"))
        other_bot = replace(self.b, bot="another-app")
        self.assertIsNotNone(media.gallery_get(self.store, other_bot, "cat"))
        self.select(self.b, "local")
        self.assertIsNone(media.gallery_get(self.store, self.b, "cat"))
        self.select(self.b, "public")
        self.assertIsNotNone(media.gallery_get(self.store, self.b, "cat"))

    def test_local_cap_shared_across_categories_but_public_accepts_101(self):
        self.select(self.a, "local")
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(self.a.scope_key, "cat", str(i), b"fixture") for i in range(100)])
        with self.assertRaisesRegex(ToolError, "100 张"):
            media.gallery_add(self.store, self.member, "waifu", png())
        self.select(self.a, "public")
        public = gallery.current(self.store, self.a)
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(public.scope, "cat", str(i), b"fixture") for i in range(100)])
        media.gallery_add(self.store, self.member, "waifu", png())
        self.assertEqual(gallery.stats(self.store, self.a)[1], 101)
        self.assertIn("不设张数上限", media.gallery_command(self.store, self.a, "status"))

    def test_public_deletion_requires_separately_authorized_root(self):
        self.select(self.a, "public")
        self.select(self.b, "public")
        item = media.gallery_add(self.store, self.member, "cat", png())
        for who in (self.a, self.b, self.member, replace(self.a, group_role="admin")):
            with self.subTest(who=who), self.assertRaisesRegex(ToolError, "总管理员"):
                media.gallery_command(self.store, who, f"del {item}")
        self.assertNotIn("/image del", community.admin_panel(self.store, self.a))
        self.assertNotIn("/group gallery", community.admin_panel(self.store, replace(self.a, group_role="admin")))
        self.assertIn("/group gallery", community.admin_panel(self.store, self.a))
        self.authorize_root()
        media.gallery_command(self.store, self.private, f"del {item}")
        self.assertIsNone(media.gallery_get(self.store, self.a, "cat"))

    def test_lists_are_paginated_without_losing_numbered_categories(self):
        self.select(self.a, "public")
        target = gallery.current(self.store, self.a)
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(target.scope, "123", str(i), b"fixture") for i in range(45)])
        target, first, total = gallery.listing(self.store, self.member, "123", 1)
        _, last, _ = gallery.listing(self.store, self.member, "123", 3)
        self.assertEqual((len(first), len(last), total), (20, 5, 45))
        self.assertTrue(target.public)
        self.assertIn("第 3/3 页", media.gallery_command(self.store, self.member, "list 123 page=3"))
        for args in ("list 123 page=4", "list 123 page=0", "list 123 page=-1", "list cat bad", "list page=inf"):
            with self.subTest(args=args), self.assertRaises(ToolError):
                media.gallery_command(self.store, self.member, args)
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(target.scope, f"c{i:03}", str(i), b"fixture") for i in range(25)])
        self.assertIn("第 2/2 页", media.gallery_command(self.store, self.member, "list page=2"))

    def test_changed_mode_while_uploading_fails_without_cross_gallery_write(self):
        self.select(self.a, "local")
        target = gallery.current(self.store, self.member)
        self.select(self.a, "public")
        with self.assertRaisesRegex(ToolError, "图库模式已改变"):
            media.gallery_add(self.store, self.member, "cat", png(), target)
        self.assertEqual(gallery.stats(self.store, self.member)[1], 0)

    def test_concurrent_last_local_slot_cannot_exceed_100(self):
        self.select(self.a, "local")
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(self.a.scope_key, "cat", str(i), b"fixture") for i in range(99)])
        def add(color):
            try:
                return media.gallery_add(self.store, self.member, "cat", png(color))
            except ToolError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, ("red", "blue")))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(gallery.stats(self.store, self.member)[1], 100)

    def test_old_schema_gallery_bytes_remain_readable_after_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            data = media.checked_image(png())
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE gallery(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, category TEXT NOT NULL, digest TEXT NOT NULL, image BLOB NOT NULL, UNIQUE(scope,category,digest))")
                db.execute("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           (self.a.scope_key, "cat", "legacy", data))
            # sqlite3's context manager commits but does not close the handle;
            # Windows cannot remove the temporary database while it is open.
            db.close()
            updated = Store(temp)
            community.management(updated, self.a, "group", "gallery local")
            self.assertEqual(media.gallery_get(updated, self.a, "cat"), data)
            self.assertEqual(gallery.stats(updated, self.a)[1], 1)


class CompleteGifTests(unittest.TestCase):
    def test_gif_bytes_frames_timing_and_loop_are_preserved(self):
        original = gif()
        result = media.checked_image(original)
        self.assertEqual(result, original)
        self.assertEqual(media.image_filename(result), "gallery.gif")
        with Image.open(io.BytesIO(result)) as animated:
            self.assertEqual(animated.n_frames, 3)
            self.assertEqual(animated.info["loop"], 3)
            durations = []
            for frame in range(animated.n_frames):
                animated.seek(frame)
                durations.append(animated.info["duration"])
            self.assertEqual(durations, [80, 160, 240])

    def test_excessive_gif_is_rejected_not_truncated(self):
        for name, limit in (("MAX_GIF_FRAMES", 2), ("MAX_GIF_TOTAL_PIXELS", 800)):
            with patch.object(media, name, limit), self.assertRaisesRegex(ToolError, "不会截取第一帧"):
                media.checked_image(gif())

    def test_corrupt_later_gif_frame_is_rejected(self):
        data = gif()
        with self.assertRaises(ToolError):
            media.checked_image(data[:-15])

    def test_static_images_still_normalized_to_jpeg(self):
        result = media.checked_image(png())
        self.assertEqual(media.image_filename(result), "gallery.jpg")
        with Image.open(io.BytesIO(result)) as picture:
            self.assertEqual(picture.format, "JPEG")
