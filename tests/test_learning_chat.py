from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi+~httpx+~websockets")

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.message import handle_event

from bot_tools import learning_commands
from bot_tools.learning_chat import LearningContent, LearningStore
from bot_tools.storage import Identity, Store, ToolError
from plugins import learning_chat, toolbox


NOW = 1788494400.0
OWNER = Identity("test", "group:one", "owner", False, "owner")
ADMIN = Identity("test", "group:one", "admin", False, "admin")
MEMBER = Identity("test", "group:one", "member", False, "member")
OTHER = Identity("test", "group:two", "other", False, "owner")


def text(value: str) -> LearningContent:
    result = LearningContent.from_text(value)
    assert result is not None
    return result


class LearningFixture:
    def init_learning(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.learning = LearningStore(self.store)

    def enable(self):
        self.learning.update(OWNER, "enabled", 1)

    def teach(self, prompt="早上好", answer="早呀", count=4):
        tick = 0
        for index in range(count):
            self.learning.observe(MEMBER, text(prompt), f"p{index}", now=NOW + tick)
            tick += 1
            self.learning.observe(OWNER, text(answer), f"a{index}", now=NOW + tick)
            tick += 1


class LearningStoreTests(LearningFixture, unittest.TestCase):
    def setUp(self):
        self.init_learning()

    def test_existing_learning_config_gets_repeat_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with store.connect() as db:
                db.execute("""
                    CREATE TABLE learning_config (
                        scope TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        answer_threshold INTEGER NOT NULL DEFAULT 4,
                        repeat_threshold INTEGER NOT NULL DEFAULT 3,
                        image_enabled INTEGER NOT NULL DEFAULT 1,
                        library_mode TEXT NOT NULL DEFAULT 'public',
                        updated REAL NOT NULL)
                """)
                db.execute("INSERT INTO learning_config(scope,updated) VALUES('legacy',0)")
            LearningStore(store)
            with store.connect() as db:
                row = db.execute("SELECT * FROM learning_config WHERE scope='legacy'").fetchone()
            self.assertEqual(row["repeat_enabled"], 1)
            self.assertEqual(row["repeat_probability"], 100)
            self.assertEqual(row["interrupt_repeat_enabled"], 1)
            self.assertEqual(row["interrupt_repeat_probability"], 25)

    def test_default_is_off_and_only_group_admin_can_change_it(self):
        self.assertFalse(self.learning.enabled(MEMBER))
        with self.assertRaises(ToolError):
            self.learning.update(MEMBER, "enabled", 1)
        self.learning.update(ADMIN, "enabled", 1)
        self.assertTrue(LearningStore(Store(self.temp.name)).enabled(MEMBER))
        self.learning.update(OWNER, "enabled", 0)
        self.assertFalse(self.learning.enabled(MEMBER))

    def test_learns_repeated_adjacent_dialogue_and_replies(self):
        self.enable()
        self.teach()
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            reply = self.learning.observe(MEMBER, text("早上好！"), "trigger", now=NOW + 20)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.text, "早呀")
        self.assertEqual(reply.reason, "learned")
        rows, total = self.learning.pairs(OWNER, 1)
        self.assertGreaterEqual(total, 2)
        self.assertTrue(any(row["reply_preview"] == "早呀" and row["count"] == 4 for row in rows))

    def test_duplicate_delivery_does_not_learn_twice(self):
        self.enable()
        self.learning.observe(MEMBER, text("问题"), "same", now=NOW)
        self.learning.observe(OWNER, text("答案"), "answer", now=NOW + 1)
        self.learning.observe(OWNER, text("答案"), "answer", now=NOW + 2)
        with self.store.connect() as db:
            count = db.execute(
                "SELECT count FROM learning_pairs WHERE scope=? AND prompt_preview='问题'",
                (OWNER.scope_key,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_private_group_does_not_use_public_learning(self):
        self.enable()
        self.teach()
        self.learning.update(OTHER, "enabled", 1)
        self.learning.update(OTHER, "library_mode", "local")
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            self.assertIsNone(self.learning.observe(OTHER, text("早上好"), "other", addressed=True, now=NOW + 20))
        self.assertEqual(self.learning.settings(OTHER)["pairs"], 0)

    def test_public_library_is_default_and_can_reply_across_groups(self):
        self.enable()
        self.teach()
        self.learning.update(OTHER, "enabled", 1)
        self.assertEqual(self.learning.settings(OTHER)["library_mode"], "public")
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            reply = self.learning.observe(OTHER, text("早上好"), "public-other", addressed=True, now=NOW + 20)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.text, "早呀")

    def test_public_library_does_not_cross_between_bots(self):
        self.enable()
        self.teach()
        other_bot = Identity("other-bot", "group:one", "owner", False, "owner")
        self.learning.update(other_bot, "enabled", 1)
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            reply = self.learning.observe(
                other_bot, text("早上好"), "other-bot-message", addressed=True, now=NOW + 20
            )
        self.assertIsNone(reply)

    def test_repeat_requires_threshold_and_two_members(self):
        self.enable()
        self.learning.observe(MEMBER, text("复读内容"), "r1", now=NOW)
        self.assertIsNone(self.learning.observe(MEMBER, text("复读内容"), "r2", now=NOW + 1))
        with patch("bot_tools.learning_chat.random.random", return_value=1):
            reply = self.learning.observe(OWNER, text("复读内容"), "r3", now=NOW + 2)
        self.assertEqual(reply.text, "复读内容")
        self.assertEqual(reply.reason, "repeat")
        self.assertIsNone(self.learning.observe(ADMIN, text("复读内容"), "r4", now=NOW + 3))

    def test_repeat_and_interrupt_switches_and_probabilities_are_applied(self):
        self.enable()
        self.learning.update(OWNER, "repeat_enabled", 0)
        self.learning.update(OWNER, "interrupt_repeat_enabled", 0)
        self.learning.observe(MEMBER, text("关闭复读"), "off-1", now=NOW)
        self.learning.observe(ADMIN, text("关闭复读"), "off-2", now=NOW + 1)
        self.assertIsNone(self.learning.observe(OWNER, text("关闭复读"), "off-3", now=NOW + 2))

        other = Identity("test", "group:other", "owner", False, "owner")
        member = Identity("test", "group:other", "member", False, "member")
        self.learning.update(other, "enabled", 1)
        self.learning.update(other, "interrupt_repeat_probability", 100)
        self.learning.observe(member, text("一定打断"), "interrupt-1", now=NOW)
        self.learning.observe(member, text("一定打断"), "interrupt-2", now=NOW + 1)
        reply = self.learning.observe(other, text("一定打断"), "interrupt-3", now=NOW + 2)
        self.assertEqual(reply.text, "打断复读！")

    def test_zero_repeat_probabilities_do_not_reply(self):
        self.enable()
        self.learning.update(OWNER, "repeat_probability", 0)
        self.learning.update(OWNER, "interrupt_repeat_probability", 0)
        self.learning.observe(MEMBER, text("概率为零"), "zero-1", now=NOW)
        self.learning.observe(MEMBER, text("概率为零"), "zero-2", now=NOW + 1)
        self.assertIsNone(self.learning.observe(OWNER, text("概率为零"), "zero-3", now=NOW + 2))

    def test_block_word_stops_new_learning_and_existing_reply(self):
        self.enable()
        self.teach("暗号", "禁止内容")
        self.learning.block_word(ADMIN, "禁止")
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            self.assertIsNone(self.learning.observe(MEMBER, text("暗号"), "blocked-trigger", addressed=True, now=NOW + 20))
        before = self.learning.settings(OWNER)["pairs"]
        self.learning.observe(MEMBER, text("禁止新内容"), "blocked", now=NOW + 21)
        self.assertEqual(self.learning.settings(OWNER)["pairs"], before)

    def test_ban_and_unban_learned_content(self):
        self.enable()
        self.teach()
        rows, _ = self.learning.pairs(OWNER, 1)
        target = next(row for row in rows if row["reply_preview"] == "早呀")
        banned = self.learning.ban_pair(ADMIN, target["id"])
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            self.assertIsNone(self.learning.observe(MEMBER, text("早上好"), "ban-trigger", addressed=True, now=NOW + 20))
        self.learning.unban(ADMIN, banned["id"])
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            self.assertEqual(self.learning.observe(MEMBER, text("早上好"), "unban-trigger", addressed=True, now=NOW + 21).text, "早呀")

    def test_image_content_can_be_learned_without_crossing_groups(self):
        self.enable()
        image = LearningContent.from_image(b"GIF89a" + b"x" * 20)
        self.assertIsNotNone(image)
        for index in range(4):
            self.learning.observe(MEMBER, text("发张图"), f"ip{index}", now=NOW + index * 2)
            self.learning.observe(OWNER, image, f"ia{index}", now=NOW + index * 2 + 1)
        with patch("bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]):
            reply = self.learning.observe(MEMBER, text("发张图"), "image-trigger", addressed=True, now=NOW + 20)
        self.assertEqual(reply.kind, "image")
        self.assertEqual(reply.image, image.image)
        self.assertEqual(self.learning.settings(OWNER)["images"], 1)

    def test_sensitive_and_remote_text_is_rejected(self):
        self.assertIsNone(LearningContent.from_text("https://example.com/path"))
        self.assertIsNone(LearningContent.from_text("token=abcdefghijklmnopqrstuvwxyz123456"))
        self.assertIsNone(LearningContent.from_text("abcdefghijklmnopqrstuvwxyz1234567890ABCDEFG"))

    def test_clear_is_owner_only_and_retains_settings_and_words(self):
        self.enable()
        self.teach()
        self.learning.block_word(OWNER, "秘密")
        with self.assertRaisesRegex(ToolError, "群主"):
            self.learning.clear(ADMIN)
        self.learning.clear(OWNER)
        state = self.learning.settings(OWNER)
        self.assertEqual(state["pairs"], 0)
        self.assertEqual(state["blocked_words"], 1)
        self.assertTrue(state["enabled"])

    def test_command_help_and_enable_warn_about_persistence(self):
        help_result = learning_commands.dispatch(self.learning, self.store, MEMBER, "")
        self.assertIn("默认关闭", help_result)
        with self.assertRaises(ToolError):
            learning_commands.dispatch(self.learning, self.store, MEMBER, "on")
        enabled = learning_commands.dispatch(self.learning, self.store, OWNER, "on")
        self.assertIn("保存群聊内容", enabled)
        status = learning_commands.dispatch(self.learning, self.store, MEMBER, "status")
        self.assertIn("已开启", status)
        self.assertLessEqual(len(status.encode()), 1800)
        changed = learning_commands.dispatch(self.learning, self.store, OWNER, "mode local")
        self.assertIn("本群私有", changed)
        self.assertEqual(self.learning.settings(OWNER)["library_mode"], "local")

    def test_reply_management_blocks_user_and_bans_text(self):
        self.enable()
        target_actor = Identity("test", "group:one", "target").actor
        with self.assertRaises(ToolError):
            learning_commands.dispatch(
                self.learning, self.store, MEMBER, "user block", reply_user="target"
            )
        blocked = learning_commands.dispatch(
            self.learning, self.store, ADMIN, "user block", reply_user="target"
        )
        self.assertIn("停止学习", blocked)
        target = Identity("test", "group:one", "target", False, "member")
        self.assertEqual(target.actor, target_actor)
        self.assertIsNone(self.learning.observe(target, text("不会保存"), "blocked-user", now=NOW))
        self.assertEqual(self.learning.settings(OWNER)["contents"], 0)
        learning_commands.dispatch(
            self.learning, self.store, ADMIN, "user unblock", reply_user="target"
        )
        self.assertIsNone(self.learning.observe(target, text("现在保存"), "unblocked-user", now=NOW + 1))
        self.assertEqual(self.learning.settings(OWNER)["contents"], 1)

        result = learning_commands.dispatch(
            self.learning, self.store, ADMIN, "ban-reply", reply_text="不许这样回答"
        )
        self.assertIn("已禁用", result)
        self.assertEqual(self.learning.settings(OWNER)["bans"], 1)

    def test_command_validation_and_private_scope(self):
        with self.assertRaisesRegex(ToolError, "群聊"):
            learning_commands.dispatch(
                self.learning,
                self.store,
                Identity("test", "private:member", "member", True),
                "status",
            )
        with self.assertRaisesRegex(ToolError, "不能设为 1"):
            learning_commands.dispatch(self.learning, self.store, OWNER, "repeat 1")
        with self.assertRaisesRegex(ToolError, "页码"):
            learning_commands.dispatch(self.learning, self.store, OWNER, "list 2")


def event(text_value: str, number: str, *, user="member", role="member", to_me=False):
    return GroupMessageCreateEvent.model_validate({
        "id": number,
        "timestamp": "2026-09-06T00:00:00Z",
        "content": text_value,
        "to_me": to_me,
        "group_id": "one",
        "group_openid": "one",
        "author": {"id": user, "member_openid": user, "member_role": role, "bot": False},
    })


class LearningEntryTests(LearningFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.init_learning()
        adapter = MagicMock()
        adapter.config = nonebot.get_driver().config
        adapter.get_name.return_value = "QQ"
        self.bot = Bot(adapter, "test", BotInfo(id="test", token="", secret="test-only"))
        self.bot.send = AsyncMock(return_value={})
        for target, attr, value in (
            (learning_chat, "get_learning", self.learning),
            (toolbox, "get_store", self.store),
        ):
            patcher = patch.object(target, attr, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_off_by_default_then_native_group_events_learn_and_reply(self):
        with patch.object(self.store, "throttle"), patch(
            "bot_tools.learning_chat.random.choices", side_effect=lambda values, **_: [values[0]]
        ):
            await handle_event(self.bot, event("开启前", "before"))
            self.bot.send.assert_not_awaited()
            await handle_event(self.bot, event("/learn on", "on", user="owner", role="owner", to_me=True))
            await handle_event(self.bot, event("/learn threshold 1", "threshold", user="owner", role="owner", to_me=True))
            await handle_event(self.bot, event("你好呀", "hello"))
            await handle_event(self.bot, event("你好", "answer", user="owner", role="owner"))
            await handle_event(self.bot, event("你好呀", "trigger", to_me=True))
        self.assertEqual(self.bot.send.await_count, 3)
        self.assertIn("你好", str(self.bot.send.await_args_list[-1]))

    async def test_bot_authored_and_slash_messages_do_not_enter_passive_learning(self):
        self.enable()
        authored = event("不会学习", "bot")
        authored.author.bot = True
        with patch.object(self.store, "throttle"):
            await handle_event(self.bot, authored)
            await handle_event(self.bot, event("/unknown secret", "slash"))
        self.assertEqual(self.learning.settings(OWNER)["contents"], 0)

    async def test_command_switch_disables_passive_learning_without_duplicate_reply(self):
        self.enable()
        self.store.grant(self.store.register(OWNER)["code"], "admin")
        toolbox.community.management(self.store, OWNER, "command", "disable learn")
        with patch.object(self.store, "throttle"):
            await handle_event(self.bot, event("不会保存", "disabled"))
            await handle_event(self.bot, event("/learn status", "disabled-command", to_me=True))
        self.assertEqual(self.learning.settings(OWNER)["contents"], 0)
        self.assertEqual(self.bot.send.await_count, 1)
        self.assertIn("已关闭 /learn", str(self.bot.send.await_args_list[0]))
