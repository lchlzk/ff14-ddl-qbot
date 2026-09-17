from __future__ import annotations

import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_tools.ai_store import AIStore
from bot_tools.gallery import current as current_gallery
from bot_tools.storage import Identity, Store, ToolError
from bot_tools.web_admin import WebAdmin, install_web_admin
from qbot_ff14.integration import register

register()


GROUP = Identity("test", "group:one", "member", False, "owner")
PRIVATE = Identity("test", "private:one", "member", True)
KEY = "fake-test-only-credential.12345678"
WEB_USER = "Admin.Owner"
WEB_PASSWORD = "Correct-Horse-2026"


class WebAdminStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.admin = WebAdmin(self.store)

    def test_account_password_is_salted_hashed_and_sessions_can_be_revoked(self):
        result = self.admin.set_account(WEB_USER, WEB_PASSWORD, now=100)
        self.assertTrue(result["created"])
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM web_accounts").fetchone()
            self.assertNotIn(WEB_PASSWORD, str(dict(row)))
            self.assertEqual(len(row["salt"]), 32)
            self.assertEqual(len(row["password_hash"]), 64)
        session, csrf = self.admin.authenticate("admin.owner", WEB_PASSWORD, "127.0.0.1", now=101)
        self.assertEqual(len(session), 64)
        self.assertEqual(len(csrf), 48)
        self.assertIsNotNone(self.admin.session(session, now=102))
        changed = self.admin.set_account("新管理员", "Another-Secure-2026", now=103)
        self.assertFalse(changed["created"])
        self.assertEqual(changed["revoked"], 1)
        self.assertIsNone(self.admin.session(session, now=104))

    def test_multiple_bots_are_listed_without_secrets_and_group_data_is_filtered(self):
        self.admin.save_bot({"app_id": "bot-a", "secret": "secret-a", "label": "机器人 A"})
        self.admin.save_bot({"app_id": "bot-b", "secret": "secret-b", "label": "机器人 B"})
        first = Identity("bot-a", "group:same", "member", False)
        second = Identity("bot-b", "group:same", "member", False)
        self.store.register(first)
        self.store.register(second)
        self.store.feedback_add(first, "A 的反馈")
        self.store.feedback_add(second, "B 的反馈")
        listing = self.admin.bots()
        self.assertEqual({item["app_id"] for item in listing["items"]}, {"bot-a", "bot-b"})
        self.assertNotIn("secret-a", repr(listing))
        self.assertNotIn("secret-b", repr(listing))
        self.assertEqual(self.admin.groups(1, bot="bot-a")["total"], 1)
        self.assertEqual(self.admin.groups(1, bot="bot-b")["total"], 1)
        self.assertIn("A 的反馈", str(self.admin.feedback(1, False, "bot-a")))
        self.assertNotIn("B 的反馈", str(self.admin.feedback(1, False, "bot-a")))
        self.admin.set_ai_enabled(False, "bot-a")
        self.assertFalse(self.admin.overview("bot-a")["ai_enabled"])
        self.assertTrue(self.admin.overview("bot-b")["ai_enabled"])

    def test_password_login_is_rate_limited_and_uses_generic_failure(self):
        self.admin.set_account(WEB_USER, WEB_PASSWORD, now=1)
        for attempt in range(5):
            with self.assertRaisesRegex(ToolError, "用户名或密码不正确"):
                self.admin.authenticate(WEB_USER, "wrong-password", "127.0.0.2", now=10 + attempt)
        with self.assertRaisesRegex(ToolError, "登录尝试过多"):
            self.admin.authenticate(WEB_USER, WEB_PASSWORD, "127.0.0.2", now=20)
        session, _ = self.admin.authenticate(WEB_USER, WEB_PASSWORD, "127.0.0.2", now=311)
        self.assertIsNotNone(self.admin.session(session, now=312))
        self.assertEqual(self.admin.revoke_sessions(), 1)
        with self.assertRaises(ToolError):
            self.admin.set_account("x", "too-short")

    def test_group_settings_are_validated_and_audited(self):
        self.store.register(GROUP)
        scope = GROUP.scope_key
        self.admin.update_group(scope, {
            "display_name": "测试群", "server": "梦羽宝境", "quota": 321, "gallery_mode": "local",
            "disabled": ["cat", "ai"],
            "learning": {"enabled": True, "answer_threshold": 5,
                         "repeat_threshold": 2, "repeat_enabled": False,
                         "repeat_probability": 40, "interrupt_repeat_enabled": True,
                         "interrupt_repeat_probability": 60, "image_enabled": False,
                         "library_mode": "local"},
        })
        row = self.admin.groups(1)["items"][0]
        self.assertEqual(row["server"], "梦羽宝境")
        self.assertEqual(row["name"], "测试群")
        self.assertEqual(row["manual_name"], "测试群")
        self.assertEqual(row["quota"], 321)
        self.assertEqual(row["gallery_mode"], "local")
        self.assertEqual(row["disabled"], ["ai", "cat"])
        self.assertTrue(row["learning"]["enabled"])
        self.assertFalse(row["learning"]["image_enabled"])
        self.assertFalse(row["learning"]["repeat_enabled"])
        self.assertEqual(row["learning"]["repeat_probability"], 40)
        self.assertTrue(row["learning"]["interrupt_repeat_enabled"])
        self.assertEqual(row["learning"]["interrupt_repeat_probability"], 60)
        self.assertEqual(row["learning"]["library_mode"], "local")
        self.assertEqual(self.admin.groups(1, "测试群")["total"], 1)
        self.assertEqual(self.admin.groups(1, "不存在")["total"], 0)
        self.assertIn("更新群设置", self.admin.audit(1)["items"][0]["action"])
        with self.assertRaises(ToolError):
            self.admin.update_group(scope, {"disabled": ["bot"]})

    def test_group_plugin_switch_only_changes_the_selected_plugin(self):
        self.store.register(GROUP)
        scope = GROUP.scope_key
        self.admin.update_group(scope, {"server": "梦羽宝境", "disabled": ["cat"]})

        self.admin.set_group_plugin(scope, "ff14", False)
        row = self.admin.groups(1)["items"][0]
        self.assertEqual(row["server"], "梦羽宝境")
        self.assertEqual(row["disabled"], ["cat", "ff14"])

        self.admin.set_group_plugin(scope, "tr", False)
        self.admin.set_group_plugin(scope, "ff14", True)
        self.assertEqual(self.admin.groups(1)["items"][0]["disabled"], ["cat", "tr"])
        self.assertEqual(self.admin.audit(1)["items"][0]["action"], "开启群插件")
        with self.assertRaises(ToolError):
            self.admin.set_group_plugin(scope, "cat", False)
        with self.assertRaises(ToolError):
            self.admin.set_group_plugin("0" * 64, "tr", False)

    def test_role_metadata_never_exposes_owner_persona_key_or_ciphertext(self):
        ai = AIStore(self.store)
        role = ai.create_role(PRIVATE, "小桃 | 私密人设")
        ai.configure(PRIVATE, "key", KEY)
        payload = self.admin.roles(1)
        text = str(payload)
        self.assertIn(role, text)
        self.assertIn("glm-4.7-flash", text)
        self.assertNotIn(KEY, text)
        self.assertNotIn("私密人设", text)
        self.assertNotIn(PRIVATE.actor, text)
        self.assertEqual(self.admin.roles(1, "小桃")["total"], 1)
        self.assertEqual(self.admin.roles(1, "完全不存在")["total"], 0)

    def test_admin_can_delete_role_and_related_runtime_data_without_resetting_usage(self):
        ai = AIStore(self.store)
        role = ai.create_role(PRIVATE, "小桃 | 私密人设")
        ai.configure(PRIVATE, "key", KEY)
        now = 2_000_000_000
        invite = ai.publish(PRIVATE, 100, now=now)
        proposal = ai.propose(GROUP, invite, now=now)
        ai.confirm(PRIVATE, proposal["code"], apply=True, now=now)
        request = ai.prepare(PRIVATE, "小桃你好", "private-run", role, now=now + 100)
        self.assertIsNotNone(request)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO ai_history(scope,prompt,reply,created) VALUES(?,?,?,?)",
                (request.scope, "旧问题", "旧回答", now + 100),
            )
        ai.publish(PRIVATE, 100, now=now + 200)
        with self.store.connect() as db:
            invite_token = db.execute(
                "SELECT token FROM ai_invites WHERE owner=?", (role,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO ai_proposals(token,invite,bot,scope,ref,expires) VALUES(?,?,?,?,?,?)",
                ("pending-proposal", invite_token, "test", GROUP.scope_key, "group-one", now + 800),
            )

        self.admin.delete_role(role, "test")

        self.assertEqual(ai.roles(PRIVATE), [])
        self.assertEqual(ai.group(GROUP), [])
        self.assertEqual(ai.usage(PRIVATE, now=now + 200)["total"], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_profiles WHERE actor=?", (role,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_groups WHERE owner=?", (role,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_invites WHERE owner=?", (role,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_history WHERE scope=?", (request.scope,)).fetchone()[0], 0)
        self.assertEqual(self.admin.audit(1)["items"][0]["action"], "删除 AI 角色")

    def test_learning_library_can_be_searched_disabled_and_restored(self):
        self.store.register(GROUP)
        with self.store.connect() as db:
            content_id = db.execute(
                "INSERT INTO learning_contents(scope,digest,kind,text,created,updated) "
                "VALUES(?,?,?,?,?,?)",
                (GROUP.scope_key, "answer-digest", "text", "这是回答", 100, 101),
            ).lastrowid
            pair_id = db.execute(
                "INSERT INTO learning_pairs(scope,prompt_key,prompt_preview,reply_content,count,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (GROUP.scope_key, "question-key", "这是问题", content_id, 4, 100, 101),
            ).lastrowid
        result = self.admin.learning(1, "回答")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["prompt"], "这是问题")
        self.admin.set_learning_pair(pair_id, True)
        self.assertEqual(self.admin.learning(1, status="disabled")["total"], 1)
        self.admin.set_learning_pair(pair_id, False)
        self.assertEqual(self.admin.learning(1, status="active")["total"], 1)

    def test_learning_library_can_be_sorted_by_learning_count(self):
        self.store.register(GROUP)
        with self.store.connect() as db:
            first_content = db.execute(
                "INSERT INTO learning_contents(scope,digest,kind,text,created,updated) VALUES(?,?,?,?,?,?)",
                (GROUP.scope_key, "first-answer", "text", "高频回答", 100, 100),
            ).lastrowid
            second_content = db.execute(
                "INSERT INTO learning_contents(scope,digest,kind,text,created,updated) VALUES(?,?,?,?,?,?)",
                (GROUP.scope_key, "second-answer", "text", "低频回答", 100, 200),
            ).lastrowid
            db.execute(
                "INSERT INTO learning_pairs(scope,prompt_key,prompt_preview,reply_content,count,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (GROUP.scope_key, "high", "高频问题", first_content, 6, 100, 100),
            )
            db.execute(
                "INSERT INTO learning_pairs(scope,prompt_key,prompt_preview,reply_content,count,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (GROUP.scope_key, "low", "低频问题", second_content, 1, 100, 200),
            )
        self.assertEqual(self.admin.learning(1)["items"][0]["prompt"], "低频问题")
        self.assertEqual(self.admin.learning(1, order="count_desc")["items"][0]["prompt"], "高频问题")
        self.assertEqual(self.admin.learning(1, order="count_asc")["items"][0]["prompt"], "低频问题")
        with self.assertRaisesRegex(ToolError, "排序方式"):
            self.admin.learning(1, order="unsafe")

    def test_gallery_filters_by_stored_scope_and_labels_private_group(self):
        self.store.register(GROUP)
        other = Identity("test", "group:two", "owner", False, "owner")
        self.store.register(other)
        public_scope = current_gallery(self.store, GROUP).scope
        self.admin.update_group(GROUP.scope_key, {"display_name": "一号测试群"})
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                (public_scope, "spk", "public-spk", b"public"),
            )
            db.execute(
                "INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                (GROUP.scope_key, "cat", "local-cat", b"local-one"),
            )
            db.execute(
                "INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                (other.scope_key, "otter", "local-otter", b"local-two"),
            )

        public = self.admin.gallery(1, "public")
        self.assertEqual([item["category"] for item in public["items"]], ["spk"])
        self.assertEqual(public["categories"], ["spk"])
        private = self.admin.gallery(1, "local")
        self.assertEqual({item["category"] for item in private["items"]}, {"cat", "otter"})
        self.assertNotIn("spk", private["categories"])
        one_group = self.admin.gallery(1, "local", group_scope=GROUP.scope_key)
        self.assertEqual([item["category"] for item in one_group["items"]], ["cat"])
        self.assertEqual(one_group["items"][0]["group_name"], "一号测试群")
        self.assertEqual(one_group["items"][0]["group_tag"], GROUP.scope_key[:10])

        # Changing the current mode must not reclassify retained private data.
        self.admin.update_group(GROUP.scope_key, {"gallery_mode": "public"})
        self.assertEqual(self.admin.gallery(1, "local", group_scope=GROUP.scope_key)["total"], 1)
        self.assertEqual(self.admin.gallery(1, "public")["total"], 1)


class WebAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.register(GROUP)
        self.feedback_id = self.store.feedback_add(GROUP, "后台测试反馈", now=100)
        with self.store.connect() as db:
            self.image_id = db.execute(
                "INSERT INTO gallery(scope,category,digest,image) VALUES(?,?,?,?)",
                (GROUP.scope_key, "cat", "test-image", b"\xff\xd8\xff\xd9"),
            ).lastrowid
        app = FastAPI()
        self.admin = install_web_admin(app, self.store)
        self.admin.set_account(WEB_USER, WEB_PASSWORD)
        self.client = TestClient(app)

    def login(self):
        response = self.client.post(
            "/admin/api/login", json={"username": WEB_USER, "password": WEB_PASSWORD},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.csrf = response.json()["data"]["csrf"]
        return response

    def post(self, path: str, body: dict):
        return self.client.post(path, json=body, headers={"X-Admin-CSRF": self.csrf})

    def test_page_security_auth_csrf_and_logout(self):
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertEqual(self.client.get("/admin/api/overview").status_code, 401)
        login = self.login()
        cookie = login.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("fake-test", self.client.get("/admin/api/overview").text)
        denied = self.client.post("/admin/api/ai-global", json={"enabled": False})
        self.assertEqual(denied.status_code, 403)
        accepted = self.post("/admin/api/ai-global", {"enabled": False})
        self.assertEqual(accepted.status_code, 200)
        self.assertFalse(self.client.get("/admin/api/overview").json()["data"]["ai_enabled"])
        self.assertEqual(self.post("/admin/api/logout", {}).status_code, 200)
        self.assertEqual(self.client.get("/admin/api/overview").status_code, 401)

    def test_role_can_be_deleted_from_admin_with_csrf_confirmation(self):
        ai = AIStore(self.store)
        role = ai.create_role(PRIVATE, "待删除角色 | 测试人设")
        self.login()
        asset = self.client.get("/admin/assets/admin.js")
        self.assertIn('data-role-delete=', asset.text)
        self.assertEqual(
            self.client.post(f"/admin/api/roles/{role}/delete", json={}).status_code,
            403,
        )
        deleted = self.post(f"/admin/api/roles/{role}/delete", {})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(role, self.client.get("/admin/api/roles?bot=test").text)
        self.assertEqual(self.post(f"/admin/api/roles/{role}/delete", {}).status_code, 400)

    def test_bot_credentials_api_never_returns_or_stores_plaintext(self):
        self.login()
        secret = "api-secret-OnlyForTests"
        created = self.post("/admin/api/bots", {
            "app_id": "api-bot", "secret": secret, "label": "API 机器人",
            "connection": "websocket", "enabled": True,
            "c2c_group_at_messages": True, "at_messages": True,
        })
        self.assertEqual(created.status_code, 200, created.text)
        listing = self.client.get("/admin/api/bots")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("API 机器人", listing.text)
        self.assertNotIn(secret, listing.text)
        with self.store.connect() as db:
            encrypted = bytes(db.execute(
                "SELECT secret FROM qq_bot_credentials WHERE app_id='api-bot'"
            ).fetchone()[0])
        self.assertNotIn(secret.encode(), encrypted)
        self.assertEqual(
            self.post("/admin/api/bots/api-bot/delete", {}).status_code, 200
        )

    def test_bot_credentials_are_live_reconciled_without_restart(self):
        credentials = self.admin.credentials

        class Runtime:
            def __init__(self):
                self.loaded = set()
                self.revisions = {}
                self.calls = []

            async def reconcile(inner, app_id):
                inner.calls.append(app_id)
                item = next(
                    (row for row in credentials.metadata() if row["app_id"] == app_id),
                    None,
                )
                if item is not None:
                    inner.revisions[app_id] = item["updated"]
                    if item["enabled"]:
                        inner.loaded.add(app_id)
                    else:
                        inner.loaded.discard(app_id)
                else:
                    inner.loaded.discard(app_id)
                    inner.revisions.pop(app_id, None)

            def snapshot(inner):
                return {
                    "loaded": set(inner.loaded), "connected": set(),
                    "connecting": set(inner.loaded), "webhook": set(),
                    "revisions": dict(inner.revisions),
                }

        runtime = Runtime()
        app = FastAPI()
        install_web_admin(app, self.store, runtime)
        client = TestClient(app)
        login = client.post(
            "/admin/api/login", json={"username": WEB_USER, "password": WEB_PASSWORD},
        )
        csrf = login.json()["data"]["csrf"]
        headers = {"X-Admin-CSRF": csrf}
        created = client.post("/admin/api/bots", json={
            "app_id": "hot-bot", "secret": "hot-secret", "label": "热加载机器人",
            "connection": "websocket", "enabled": True,
            "c2c_group_at_messages": True, "at_messages": True,
        }, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["data"]["state"], "connecting")
        listing = client.get("/admin/api/bots").json()["data"]
        item = next(row for row in listing["items"] if row["app_id"] == "hot-bot")
        self.assertFalse(item["restart_required"])
        self.assertEqual(runtime.calls, ["hot-bot"])

        disabled = client.post("/admin/api/bots", json={
            "app_id": "hot-bot", "secret": "", "label": "热加载机器人",
            "connection": "websocket", "enabled": False,
            "c2c_group_at_messages": True, "at_messages": True,
        }, headers=headers)
        self.assertEqual(disabled.status_code, 200, disabled.text)
        listing = client.get("/admin/api/bots").json()["data"]
        item = next(row for row in listing["items"] if row["app_id"] == "hot-bot")
        self.assertEqual(item["state"], "disabled")
        self.assertFalse(item["restart_required"])
        self.assertNotIn("hot-bot", runtime.loaded)

        deleted = client.post(
            "/admin/api/bots/hot-bot/delete", json={}, headers=headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(runtime.calls, ["hot-bot", "hot-bot", "hot-bot"])
        self.assertNotIn("hot-bot", runtime.loaded)

    def test_feedback_gallery_and_group_mutations(self):
        self.login()
        response = self.client.get("/admin/api/feedback")
        self.assertIn("后台测试反馈", response.text)
        self.assertEqual(
            self.post(f"/admin/api/feedback/{self.feedback_id}", {"closed": True}).status_code,
            200,
        )
        self.assertEqual(self.client.get("/admin/api/feedback").json()["data"]["total"], 0)
        image = self.client.get(f"/admin/api/gallery/{self.image_id}/image")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertEqual(
            self.post(f"/admin/api/gallery/{self.image_id}/delete", {}).status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/admin/api/gallery/{self.image_id}/image").status_code, 404
        )
        updated = self.post(f"/admin/api/groups/{GROUP.scope_key}", {
            "display_name": "网页测试群", "server": "红玉海", "quota": 222, "gallery_mode": "public", "disabled": [],
            "learning": {"enabled": False, "answer_threshold": 4,
                         "repeat_threshold": 3, "image_enabled": True,
                         "library_mode": "public"},
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIn("红玉海", self.client.get("/admin/api/groups").text)
        self.assertIn("网页测试群", self.client.get("/admin/api/groups").text)
        self.assertEqual(self.client.get("/admin/api/groups?q=not-found").json()["data"]["total"], 0)

        switched = self.post(f"/admin/api/groups/{GROUP.scope_key}/plugin", {
            "command": "ff14", "enabled": False,
        })
        self.assertEqual(switched.status_code, 200, switched.text)
        group = self.client.get("/admin/api/groups").json()["data"]["items"][0]
        self.assertIn("ff14", group["disabled"])
        self.assertNotIn("tr", group["disabled"])
        invalid = self.post(f"/admin/api/groups/{GROUP.scope_key}/plugin", {
            "command": "cat", "enabled": False,
        })
        self.assertEqual(invalid.status_code, 400)
        invalid_type = self.post(f"/admin/api/groups/{GROUP.scope_key}/plugin", {
            "command": "tr", "enabled": "false",
        })
        self.assertEqual(invalid_type.status_code, 400)

    def test_cross_origin_mutation_and_wrong_password_are_rejected(self):
        self.assertEqual(self.client.post(
            "/admin/api/login", json={"username": WEB_USER, "password": WEB_PASSWORD},
        ).status_code, 200)
        self.csrf = self.client.get("/admin/api/session").json()["data"]["csrf"]
        denied_login = TestClient(self.client.app).post(
            "/admin/api/login", json={"username": WEB_USER, "password": "wrong-password"},
        )
        self.assertEqual(denied_login.status_code, 400)
        self.assertNotIn("hash", denied_login.text.lower())
        response = self.client.post(
            "/admin/api/ai-global", json={"enabled": False},
            headers={"X-Admin-CSRF": self.csrf, "Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_learning_api_search_filter_and_toggle(self):
        with self.store.connect() as db:
            content_id = db.execute(
                "INSERT INTO learning_contents(scope,digest,kind,text,created,updated) "
                "VALUES(?,?,?,?,?,?)",
                (GROUP.scope_key, "api-answer", "text", "网页回答", 100, 101),
            ).lastrowid
            pair_id = db.execute(
                "INSERT INTO learning_pairs(scope,prompt_key,prompt_preview,reply_content,count,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (GROUP.scope_key, "api-question", "网页问题", content_id, 3, 100, 101),
            ).lastrowid
        self.login()
        found = self.client.get("/admin/api/learning?q=网页回答").json()["data"]
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["id"], pair_id)
        self.assertEqual(
            self.post(f"/admin/api/learning/{pair_id}/disable", {}).status_code, 200
        )
        disabled = self.client.get("/admin/api/learning?status=disabled").json()["data"]
        self.assertEqual(disabled["total"], 1)
        self.assertEqual(
            self.post(f"/admin/api/learning/{pair_id}/restore", {}).status_code, 200
        )
        self.assertEqual(
            self.client.get("/admin/api/learning?status=disabled").json()["data"]["total"], 0
        )


if __name__ == "__main__":
    unittest.main()
