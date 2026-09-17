from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi+~httpx+~websockets")

from bot_tools import admin, community
from bot_tools.security import redact_binding_log
from bot_tools.storage import Identity, Store, ToolError
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from plugins import toolbox


class BindingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)
        self.private = Identity("app", "private:me", "me", True)
        self.group = Identity("app", "group:a", "me", False, "owner")

    def count(self, table):
        assert table in {"identities", "admin_bindings", "admin_tokens"}
        with self.store.connect() as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_two_step_private_binding_never_grants_on_redemption(self):
        token = self.store.create_admin_token()
        self.assertRegex(token, r"^[a-f0-9]{32}$")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM admin_tokens").fetchone()
            self.assertEqual(row["digest"], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(token, str(dict(row)))
        text = community.management(self.store, self.private, "bot", "whoami " + token)
        code = re.search(r"身份码：([a-f0-9]{16})", text).group(1)
        self.assertNotIn(token, text)
        self.assertIn("15 分钟", text)
        self.assertFalse(self.store.is_admin(self.private, owner=True))
        self.assertEqual(self.count("admin_tokens"), 0)
        self.store.grant(code, "owner")
        self.assertTrue(self.store.is_admin(self.private, owner=True))
        self.assertEqual(self.count("admin_bindings"), 0)
        with self.assertRaises(ToolError):
            self.store.grant(code, "owner")
        self.store.grant(code, "member")
        self.assertFalse(self.store.is_admin(self.private))
        with self.assertRaises(ToolError):
            self.store.grant(code, "owner")

    def test_public_bot_and_admin_help_never_issue_codes(self):
        for who in (replace(self.group, group_role="member"), self.private):
            for command, args in (("bot", ""), ("bot", "whoami"), ("bot", "status"),
                                  ("bot", "token"), ("bot", "admin token"),
                                  ("group", ""), ("command", "list")):
                with self.subTest(command=command, args=args), self.assertRaises(ToolError) as err:
                    community.management(self.store, who, command, args)
                self.assertNotIn("admin add", str(err.exception))
                self.assertNotIn("身份码", str(err.exception))
        self.assertEqual(self.count("identities"), 0)

    def test_group_panels_only_show_real_current_group_capabilities(self):
        for role, label in (("owner", "QQ群主"), ("admin", "QQ管理员")):
            for args in ("", "whoami"):
                text = community.management(self.store, replace(self.group, group_role=role), "bot", args)
                self.assertIn(label, text)
                for cmd in ("/custom_reply", "/command", "/group", "/hunt", "/vote", "/lottery"):
                    self.assertIn(cmd, text)
                # The default public gallery can only be deleted by a bot
                # superadmin; QQ owners can switch to a private group gallery.
                if role == "owner":
                    self.assertIn("/group gallery", text)
                self.assertNotIn("图库管理 · /image", text)
                for hidden in ("身份码", "授权码", "admin add", "/comment", "bot.cmd", "bot.sh", "/bot status"):
                    self.assertNotIn(hidden, text)
                self.assertLessEqual(len(text.encode()), 1800)
        self.assertEqual(self.count("identities"), 0)

    def test_private_admin_panel_has_feedback_without_binding_codes(self):
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        self.store.grant(record["code"], "owner")
        text = community.management(self.store, self.private, "bot", "")
        self.assertIn("机器人总管理员", text)
        self.assertIn("/comment list", text)
        self.assertIn("/bot status", text)
        self.assertNotIn(record["code"], text)
        self.assertNotIn("admin add", text)
        self.assertLessEqual(len(text.encode()), 1800)

    def test_bot_status_denies_group_roles_and_delegated_admins(self):
        self.store.grant(self.store.register(self.private)["code"], "admin")
        for who in (self.group, replace(self.group, group_role="admin"), self.private):
            with self.subTest(who=who), self.assertRaisesRegex(ToolError, "总管理员"):
                community.management(self.store, who, "bot", "status")
            self.assertNotIn("/bot status", community.management(self.store, who, "bot", ""))
            with self.assertRaises(ToolError) as err:
                community.management(self.store, who, "bot", "unknown")
            self.assertNotIn("/bot status", str(err.exception))
        # A separate manual scoped grant also cannot grant global status access.
        self.store.grant(self.store.register(self.group)["code"], "admin")
        with self.assertRaisesRegex(ToolError, "总管理员"):
            community.management(self.store, self.group, "bot", "status")

    def test_bot_status_requires_current_server_confirmed_superadmin(self):
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        with self.assertRaises(ToolError):
            community.management(self.store, self.private, "bot", "status")
        self.store.grant(record["code"], "owner")
        self.assertIn("工具箱运行正常", community.management(self.store, self.private, "bot", "status"))
        self.store.grant(record["code"], "member")
        with self.assertRaises(ToolError):
            community.management(self.store, self.private, "bot", "status")

    def test_group_redemption_does_not_consume_token_even_for_group_owner(self):
        token = self.store.create_admin_token()
        for who in (self.group, replace(self.group, group_role="admin"),
                    replace(self.group, private=True), replace(self.private, private=False)):
            with self.assertRaises(ToolError):
                self.store.redeem_admin_token(who, token)
        with self.assertRaises(ToolError):
            community.management(self.store, self.group, "bot", "whoami " + token)
        self.assertEqual(self.count("identities"), 0)
        self.assertEqual(self.count("admin_tokens"), 1)
        self.store.redeem_admin_token(self.private, token)

    def test_invalid_tokens_never_issue_identities(self):
        token = self.store.create_admin_token(now=100)
        for i, bad in enumerate(("", "xxx", "f" * 32, "' OR 1=1", token + "a")):
            with self.assertRaises(ToolError):
                self.store.redeem_admin_token(self.private, bad, now=100 + i * 4)
        self.assertEqual(self.count("identities"), 0)
        self.assertEqual(self.count("admin_tokens"), 1)

    def test_token_expiry_and_regeneration(self):
        token = self.store.create_admin_token(now=100)
        with self.assertRaises(ToolError):
            self.store.redeem_admin_token(self.private, token, now=700)
        new = self.store.create_admin_token(now=800)
        with self.assertRaises(ToolError):
            self.store.redeem_admin_token(self.private, token, now=804)
        self.store.redeem_admin_token(self.private, new.upper(), now=808)
        with self.assertRaises(ToolError):
            self.store.redeem_admin_token(self.private, new, now=812)

    def test_pending_confirmation_expires(self):
        token = self.store.create_admin_token(now=100)
        record = self.store.redeem_admin_token(self.private, token, now=110)
        with patch("bot_tools.storage.time.time", return_value=1010), self.assertRaises(ToolError):
            self.store.grant(record["code"], "owner")
        self.assertFalse(self.store.is_admin(self.private))

    def test_legacy_codes_cannot_bypass_private_invitation(self):
        for who in (self.private, self.group):
            code = self.store.register(who)["code"]
            with self.assertRaises(ToolError):
                self.store.grant(code, "owner")
            with self.assertRaises(ToolError):
                self.store.grant(code, "owner", who.scope_key)
        old = self.store.register(self.private)["code"]
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        self.assertNotEqual(old, record["code"])
        with self.assertRaises(ToolError):
            self.store.grant(old, "owner")
        self.store.grant(record["code"], "owner")

    def test_new_redemption_replaces_pending_code_without_changing_role(self):
        with patch("bot_tools.storage.time.time", return_value=100):
            first = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
            self.store.grant(first["code"], "owner")
        with patch("bot_tools.storage.time.time", return_value=200):
            second = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
            self.assertTrue(self.store.is_admin(self.private, owner=True))
            self.assertNotEqual(first["code"], second["code"])
            with self.assertRaises(ToolError):
                self.store.grant(first["code"], "owner")
            self.store.grant(second["code"], "member")
            with self.assertRaises(ToolError):
                self.store.grant(second["code"], "owner")

    def test_redemption_atomic_and_single_winner_across_users(self):
        token = self.store.create_admin_token()
        def redeem(index):
            who = Identity("app", f"private:{index}", str(index), True)
            try:
                return self.store.redeem_admin_token(who, token)
            except ToolError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(redeem, range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.count("identities"), 1)
        self.assertEqual(self.count("admin_bindings"), 1)
        self.assertEqual(self.count("admin_tokens"), 0)

    def test_server_confirmation_is_atomic_and_single_use(self):
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        def confirm(_):
            try:
                self.store.grant(record["code"], "owner")
                return True
            except ToolError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(confirm, range(4))), 1)
        self.assertTrue(self.store.is_admin(self.private, owner=True))

    def test_redemption_rolls_back_if_identity_write_fails(self):
        old = self.store.register(self.group)["code"]
        token = self.store.create_admin_token(now=100)
        with patch("bot_tools.storage.secrets.token_hex", return_value=old), self.assertRaises(sqlite3.IntegrityError):
            self.store.redeem_admin_token(self.private, token, now=104)
        self.assertEqual(self.count("admin_tokens"), 1)
        self.assertEqual(self.count("admin_bindings"), 0)
        self.store.redeem_admin_token(self.private, token, now=108)

    def test_same_actor_is_rate_limited(self):
        token = self.store.create_admin_token(now=100)
        with self.assertRaises(ToolError):
            self.store.redeem_admin_token(self.private, "invalid", now=100)
        with self.assertRaisesRegex(ToolError, "操作太快"):
            self.store.redeem_admin_token(self.private, token, now=101)
        self.store.redeem_admin_token(self.private, token, now=103)

    def test_pending_binding_survives_process_restart(self):
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        reopened = Store(self.store.path)
        reopened.grant(record["code"], "owner")
        self.assertTrue(self.store.is_admin(self.private, owner=True))
        self.assertFalse(self.store.is_admin(replace(self.private, bot="other-app"), owner=True))
        self.assertFalse(self.store.is_admin(replace(self.group, group_role="member")))

    def test_existing_grants_and_feedback_preserved(self):
        record = self.store.register(self.private)
        with self.store.connect() as db:
            db.execute("UPDATE identities SET role='owner' WHERE actor=?", (self.private.actor,))
        n = self.store.feedback_add(self.group, "历史反馈")
        reopened = Store(self.store.path)
        self.assertTrue(reopened.is_admin(self.private, owner=True))
        self.assertEqual(reopened.feedback_list(self.private, 1)[0]["id"], n)
        self.assertEqual(reopened.register(self.private)["code"], record["code"])

    def test_cli_token_then_final_confirmation_and_revoke(self):
        out = io.StringIO()
        with patch.object(admin, "Store", return_value=self.store), patch("sys.argv", ["admin", "token"]), redirect_stdout(out):
            admin.main()
        token = re.search(r"/bot whoami ([a-f0-9]{32})", out.getvalue()).group(1)
        record = self.store.redeem_admin_token(self.private, token)
        for action in ("add", "remove"):
            with patch.object(admin, "Store", return_value=self.store), patch("sys.argv", ["admin", action, record["code"]]), redirect_stdout(io.StringIO()):
                admin.main()
            self.assertEqual(self.store.is_admin(self.private, owner=True), action == "add")
        with patch.object(admin, "Store", return_value=self.store), patch("sys.argv", ["admin", "add", record["code"]]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            admin.main()

    def test_binding_secret_masked_in_event_logs(self):
        token = "a1" * 16
        for content in (f"/bot whoami {token}", f"/bot   whoami  {token.upper()}",
                        f"Event(content='/bot whoami {token}')"):
            record = {"message": content}
            redact_binding_log(record)
            self.assertNotIn(token, record["message"].lower())
            self.assertIn("授权码已隐藏", record["message"])
        record = {"message": "normal /bot whoami"}
        redact_binding_log(record)
        self.assertEqual(record["message"], "normal /bot whoami")


class BindingDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_status_dispatch_denies_native_group_admins(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            bot = SimpleNamespace(self_id="app")
            for role in ("owner", "admin"):
                event = GroupAtMessageCreateEvent.model_validate({
                    "id":"group-test", "content":"/bot status", "to_me":True,
                    "timestamp":"2026-09-04T00:00:00Z", "group_id":"a", "group_openid":"a",
                    "author":{"id":"me", "member_openid":"me", "member_role":role, "bot":False},
                })
                with patch.object(toolbox, "get_store", return_value=store), patch.object(store, "throttle"):
                    text = await toolbox.dispatch(bot, event, "bot", "")
                    self.assertNotIn("/bot status", text)
                    with self.assertRaisesRegex(ToolError, "总管理员"):
                        await toolbox.dispatch(bot, event, "bot", "status")

    async def test_nonebot_event_logging_and_private_command_integration(self):
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.config import BotInfo
        from nonebot.log import logger
        from nonebot.message import handle_event

        adapter = MagicMock()
        adapter.config = nonebot.get_driver().config
        adapter.get_name.return_value = "QQ"
        bot = Bot(adapter, "test", BotInfo(id="test", token="", secret="test-only"))
        bot.send = AsyncMock(return_value={})
        output = io.StringIO()
        sink = logger.add(output, format="{message}", level="DEBUG")
        logger.configure(patcher=redact_binding_log)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                token = store.create_admin_token()
                event = C2CMessageCreateEvent.model_validate({
                    "id":"private-test", "content":"/bot whoami " + token, "to_me":True,
                    "timestamp":"2026-09-04T00:00:00Z", "author":{"id":"me", "user_openid":"me"},
                })
                with patch.object(toolbox, "get_store", return_value=store):
                    await handle_event(bot, event)
                self.assertTrue(bot.send.called)
                reply = str(bot.send.call_args.kwargs["message"])
                self.assertIn("身份码", reply)
                self.assertNotIn(token, reply)
                self.assertFalse(store.is_admin(toolbox.identity(bot, event), owner=True))
                self.assertNotIn(token, output.getvalue())
                self.assertIn("授权码已隐藏", output.getvalue())
        finally:
            logger.remove(sink)
            logger.configure(patcher=None)

    async def test_dispatch_does_not_register_normal_users_and_private_token_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            bot = SimpleNamespace(self_id="app")
            event = GroupAtMessageCreateEvent.model_validate({
                "id":"group-test", "content":"/bot", "timestamp":"2026-09-04T00:00:00Z", "to_me":True,
                "group_id":"a", "group_openid":"a", "author":{"id":"me", "member_openid":"me", "member_role":"member", "bot":False},
            })
            private = C2CMessageCreateEvent.model_validate({
                "id":"private-test", "content":"/bot", "timestamp":"2026-09-04T00:00:00Z",
                "author":{"id":"me", "user_openid":"me"},
            })
            with patch.object(toolbox, "get_store", return_value=store), patch.object(store, "throttle"):
                for e in (event, private):
                    for raw in ("", "help", "whoami", "status"):
                        with self.assertRaises(ToolError):
                            await toolbox.dispatch(bot, e, "bot", raw)
                    await toolbox.dispatch(bot, e, "toolbox", "")
                with store.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM identities").fetchone()[0], 0)
                token = store.create_admin_token()
                with self.assertRaises(ToolError):
                    await toolbox.dispatch(bot, event, "bot", "whoami " + token)
                text = await toolbox.dispatch(bot, private, "bot", "whoami " + token)
                code = re.search(r"身份码：([a-f0-9]{16})", text).group(1)
                with self.assertRaises(ToolError):
                    await toolbox.dispatch(bot, private, "bot", "")
                store.grant(code, "owner")
                text = await toolbox.dispatch(bot, private, "bot", "")
                self.assertIn("/comment list", text)
                self.assertNotIn(code, text)


if __name__ == "__main__":
    unittest.main()
