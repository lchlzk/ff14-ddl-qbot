from __future__ import annotations

import tempfile
import unittest

from bot_tools import group_status
from bot_tools.ai_store import AIStore
from bot_tools.storage import Identity, Store, ToolError
from qbot_ff14.integration import register

register()


class GroupStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.ai = AIStore(self.store)
        self.member = Identity("bot", "group:one", "member", False, "member")
        self.admin = Identity("bot", "group:one", "admin", False, "admin")
        self.owner = Identity("bot", "group:one", "owner", False, "owner")
        self.other = Identity("bot", "group:two", "owner", False, "owner")
        with self.store.state(self.admin.scope_key) as doc:
            doc.update({
                "server": "梦羽宝境", "quota": 250, "gallery_mode": "public",
                "disabled": ["cat", "not-a-command"],
                "replies": {"你好": "你好呀", "晚安": "晚安"},
                "hunt_rules": {"绝牙": {"min": 4, "max": 6}},
            })
        with self.store.state("ai:global") as doc:
            doc["enabled"] = False
        with self.store.connect() as db:
            public = group_status.gallery.current(self.store, self.admin)
            db.execute("INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                       (public.scope, "cat", "fixture", b"image"))
            for role, sponsor, name, provider, region, paused in (
                    ("role-one", "owner-hash-one", "小桃", "glm", "cn", 0),
                    ("role-two", "owner-hash-two", "阿雪", "qwen", "beijing", 1)):
                db.execute("INSERT INTO ai_profiles(actor,owner,bot,name,provider,region,revision) VALUES(?,?,?,?,?,?,?)",
                           (role, sponsor, "bot", name, provider, region, "revision-" + role))
                db.execute("INSERT INTO ai_groups(scope,bot,ref,id,owner,sponsor,cap,paused) VALUES(?,?,?,?,?,?,?,?)",
                           (self.admin.scope_key, "bot", "group:one", "id-" + role, role, sponsor, 80, paused))

    def test_admin_overview_contains_settings_hints_and_minimal_role_rows(self):
        text = group_status.overview(self.store, self.admin)
        for expected in ("本群设置总览", "狩猎默认小区：梦羽宝境", "每人每天 250 次",
                         "公共图库 · 不设张数上限", "/cat", "关键词：2 条",
                         "狩猎规则：1 条", "AI 聊天：全局关闭 · 本群启用 · 本群 2 个角色",
                         "/group server 梦羽宝境", "/group quota 100",
                         "/group gallery public 或 local", "/image add 分类",
                         "/command list", "/command disable cat", "/command enable cat",
                         "/custom_reply set 关键词 | 内容",
                         "/hunt rule 怪物", "/command disable ai", "/ai 全局 off 或 on",
                         "小桃\n角色编号 id-role-one", "阿雪\n角色编号 id-role-two",
                         "/ai 暂停 编号", "/ai 恢复 编号", "/ai 清空 编号",
                         "/ai 移除 编号"):
            self.assertIn(expected, text)
        self.assertNotIn("默认 FFXIV 服务器", text)
        self.assertNotIn("/command disable 命令", text)
        for hidden in ("owner-hash-one", "owner-hash-two", "所属群友", "智谱 GLM",
                       "千问 Character", "小桃 · 启用", "阿雪 · 暂停", "每日 80"):
            self.assertNotIn(hidden, text)

    def test_owner_and_recognized_bot_admin_can_view_but_ordinary_members_cannot(self):
        self.assertIn("本群设置总览", group_status.overview(self.store, self.owner))
        root = Identity("bot", "group:one", "root", False, "member")
        record = self.store.register(root)
        with self.store.connect() as db:
            # Fixture for an already server-recognized owner in this scope.
            db.execute("UPDATE identities SET role='owner' WHERE actor=?", (root.actor,))
        self.assertEqual(self.store.role(root), "owner")
        self.assertIn("本群设置总览", group_status.overview(self.store, root))
        with self.assertRaisesRegex(ToolError, "群主/管理员"):
            group_status.overview(self.store, self.member)
        private = Identity("bot", "private:root", "root", True)
        with self.assertRaisesRegex(ToolError, "目标 QQ 群"):
            group_status.overview(self.store, private)

    def test_page_validation_and_empty_page(self):
        self.assertIn("本页没有 AI 角色", group_status.overview(self.store, self.admin, "2"))
        for page in ("0", "1001", "x", "1 2"):
            with self.subTest(page=page), self.assertRaises(ToolError):
                group_status.overview(self.store, self.admin, page)
