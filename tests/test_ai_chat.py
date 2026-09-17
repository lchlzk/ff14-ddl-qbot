from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import nonebot
from cryptography.fernet import Fernet

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi+~httpx+~websockets")

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.log import logger
from nonebot.message import handle_event

from bot_tools import ai_commands, ai_provider
from bot_tools.ai_store import AIStore, PRIVATE_DAILY, TTL, conversation
from bot_tools.security import redact_binding_log
from bot_tools.storage import Identity, Store, ToolError
from plugins import ai_chat, toolbox


KEY = "fake-test-only-credential.12345678"
NOW = 1788494400.0
P1 = Identity("test", "private:one", "one", True)
P2 = Identity("test", "private:two", "two", True)
G1 = Identity("test", "group:one", "unrelated-group-openid", False, "member")
G2 = Identity("test", "group:two", "different-group-openid", False, "member")
RESULT = ai_provider.Completion("（挥手）你好呀！", 100, 20, True)


class AIFixture:
    def init_store(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Store(self.temp.name)
        self.ai = AIStore(self.base)

    def role(self, who=P1, name="小桃", key=KEY):
        role = self.ai.create_role(who, name + " | 温柔活泼，简短聊天")
        self.ai.configure(who, "key", key)
        return role

    def bind(self, who=P1, group=G1, cap=10):
        code = self.ai.publish(who, cap, NOW)
        proposal = self.ai.propose(group, code, now=NOW)
        return self.ai.confirm(who, proposal["code"], apply=True, now=NOW)


class AIStoreTests(AIFixture, unittest.TestCase):
    def setUp(self):
        self.init_store()

    def test_help_does_not_register_an_identity_or_create_role(self):
        help_text = ai_commands.dispatch(self.ai, P1, "设置")
        self.assertIn("新建", help_text)
        self.assertIn("/ai 删除角色 角色编号 确认", help_text)
        self.assertEqual(self.ai.roles(P1), [])
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM identities").fetchone()[0], 0)

    def test_keys_are_encrypted_and_stay_owned_after_restart(self):
        role = self.role()
        self.assertNotIn(KEY, json.dumps(self.ai.profile(P1)))
        self.assertNotIn(KEY, json.dumps(self.ai.roles(P1)))
        with self.base.connect() as db:
            encrypted = db.execute("SELECT secret FROM ai_profiles").fetchone()[0]
            self.assertNotIn(KEY.encode(), encrypted)
        again = AIStore(Store(self.temp.name))
        request = again.prepare(P1, "你好小桃！", "1", role, NOW)
        self.assertEqual(request.key, KEY)
        self.assertNotIn(KEY, repr(request))

    def test_key_missing_or_wrong_does_not_silently_reset(self):
        role = self.role()
        path = Path(self.temp.name) / "secrets" / "ai-master.key"
        original = path.read_bytes()
        path.rename(path.with_suffix(".backup"))
        with self.assertRaisesRegex(ToolError, "主密钥丢失"):
            self.ai.prepare(P1, "小桃", "1", role, NOW)
        self.assertFalse(path.exists())
        # Fixture-only corruption, never a production file.
        path.write_bytes(Fernet.generate_key())
        with self.assertRaisesRegex(ToolError, "无法解密"):
            self.ai.prepare(P1, "小桃", "2", role, NOW)
        path.write_bytes(original)
        self.assertIsNotNone(self.ai.prepare(P1, "小桃", "3", role, NOW))

    def test_cross_role_ciphertext_swap_is_rejected(self):
        first = self.role()
        second = self.role(P2, "阿雪", "another-fake-test-key.012345678")
        with self.base.connect() as db:
            secret = db.execute("SELECT secret FROM ai_profiles WHERE actor=?", (first,)).fetchone()[0]
            db.execute("UPDATE ai_profiles SET secret=? WHERE actor=?", (secret, second))
        with self.assertRaisesRegex(ToolError, "无法解密"):
            self.ai.prepare(P2, "阿雪", "2", second, NOW)

    def test_multi_user_multi_role_and_multi_group_ownership(self):
        role_a = self.role(P1, "小桃")
        self.bind(P1, G1)
        self.bind(P1, G2)
        role_b = self.role(P1, "阿雪")
        with self.assertRaisesRegex(ToolError, "先私聊撤销"):
            self.bind(P1, G1)
        self.ai.revoke(P1, next(g["id"] for g in self.ai.groups(P1) if g["group_tag"] == G2.scope_key[:10]))
        self.bind(P1, G2)
        role_c = self.role(P2, "小雨")
        self.bind(P2, G1)
        self.assertEqual(set(self.ai.targets(G1, "叫小桃和小雨来吧")), {role_a, role_c})
        self.assertEqual(self.ai.targets(G1, "阿雪"), [])
        self.assertEqual(self.ai.targets(G2, "阿雪"), [role_b])
        self.assertEqual(set(self.ai.targets(P1, "小桃和阿雪")), {role_a, role_b})
        self.assertEqual(self.ai.targets(P2, "小桃和阿雪"), [])

    def test_name_anywhere_and_multiple_matches_not_commands(self):
        first = self.role()
        second = self.role(P1, "桃")
        self.assertEqual(set(self.ai.targets(P1, "今天的桃子让我想到了小桃")), {first, second})
        self.assertEqual(self.ai.targets(P1, "/ai 角色 小桃 | 改人设"), [])
        self.assertEqual(self.ai.targets(P1, "普通消息"), [])

    def test_cross_user_cross_bot_private_permission_checks(self):
        role = self.role()
        with self.assertRaises(ToolError):
            self.ai.select(P2, role)
        with self.assertRaises(ToolError):
            self.ai.delete_role(P2, role)
        for action, value in (("key", KEY), ("role", "阿雪 | 测试"), ("limit", "100"),
                              ("model", "glm-4.7-flash")):
            with self.assertRaisesRegex(ToolError, "私聊"):
                self.ai.configure(G1, action, value)
        self.assertIsNone(self.ai.prepare(P2, "小桃", "1", role, NOW))
        self.assertIsNone(self.ai.prepare(replace(P1, bot="other"), "小桃", "1", role, NOW))

    def test_any_group_member_can_join_but_only_private_owner_can_authorize(self):
        self.role()
        invitation = self.ai.publish(P1, 10, NOW)
        proposal = self.ai.propose(G1, invitation, now=NOW)
        with self.assertRaises(ToolError):
            self.ai.confirm(P2, proposal["code"], apply=True, now=NOW)
        with self.assertRaises(ToolError):
            self.ai.confirm(G1, proposal["code"], apply=True, now=NOW)
        self.assertEqual(self.ai.group(G1), [])
        self.assertEqual(self.ai.confirm(P1, proposal["code"], now=NOW)["group"], G1.scope_key[:10])
        self.ai.confirm(P1, proposal["code"], apply=True, now=NOW)
        with self.assertRaises(ToolError):
            self.ai.confirm(P1, proposal["code"], apply=True, now=NOW)

    def test_invites_expire_and_are_scoped_to_bot_and_role(self):
        role = self.role()
        invitation = self.ai.publish(P1, 10, NOW)
        with self.assertRaises(ToolError):
            self.ai.propose(replace(G1, bot="other"), invitation, now=NOW)
        with self.assertRaises(ToolError):
            self.ai.propose(G1, invitation, now=NOW+TTL+1)
        invitation = self.ai.publish(P1, 10, NOW)
        proposal = self.ai.propose(G1, invitation, now=NOW)
        self.role(P1, "阿雪")  # changing selection must NOT change invite's role.
        self.ai.confirm(P1, proposal["code"], apply=True, now=NOW)
        self.assertEqual(self.ai.targets(G1, "小桃"), [role])

    def test_another_group_cannot_overwrite_existing_proposal(self):
        self.role()
        invitation = self.ai.publish(P1, 10, NOW)
        first = self.ai.propose(G1, invitation, now=NOW)
        second = self.ai.propose(G2, invitation, now=NOW)
        self.ai.confirm(P1, first["code"], apply=True, now=NOW)
        self.assertEqual(len(self.ai.group(G1)), 1)
        self.assertEqual(self.ai.group(G2), [])
        with self.assertRaises(ToolError):
            self.ai.confirm(P1, second["code"], apply=True, now=NOW)

    def test_private_five_total_across_roles_restarts_and_role_deletion(self):
        first = self.role()
        second = self.role(P1, "阿雪")
        for index in range(PRIVATE_DAILY):
            role, name = (first, "小桃") if index % 2 else (second, "阿雪")
            request = self.ai.prepare(P1, name, str(index), role, NOW+index*6)
            self.ai.finish(request, RESULT, NOW+index*6)
        self.assertEqual(self.ai.usage(P1, NOW)["private"], 5)
        self.ai.delete_role(P1, second)
        third = self.role(P1, "小雨")
        with self.assertRaisesRegex(ToolError, "私聊每日 5 次"):
            AIStore(Store(self.temp.name)).prepare(P1, "小雨", "over", third, NOW+60)
        self.assertIsNotNone(self.ai.prepare(P1, "小雨", "tomorrow", third, NOW+86400))

    def test_concurrent_last_quota_is_atomic(self):
        first = self.role()
        second = self.role(P1, "阿雪")
        self.ai.configure(P1, "limit", "1")
        def attempt(role, text):
            try:
                return self.ai.prepare(P1, text, role, role, NOW)
            except ToolError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: attempt(*pair), [(first, "小桃"), (second, "阿雪")]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.ai.usage(P1, NOW)["total"], 1)

    def test_duplicate_id_is_per_role_and_errors_remain_counted(self):
        first = self.role()
        request = self.ai.prepare(P1, "小桃", "same", first, NOW)
        self.ai.finish(request, None, NOW)
        self.assertIsNone(self.ai.prepare(P1, "小桃", "same", first, NOW+6))
        second = self.role(P1, "阿雪")
        request = self.ai.prepare(P1, "阿雪", "same", second, NOW+6)
        self.assertIsNotNone(request)
        self.ai.finish(request, RESULT, NOW+6)
        self.assertFalse(self.ai.finish(request, RESULT, NOW+7))
        self.assertEqual(self.ai.usage(P1, NOW), {"total": 2, "private": 2, "input": 100, "output": 20, "unknown": 1})

    def test_group_limit_is_per_role_and_owner_total_spans_roles_groups(self):
        first = self.role()
        self.bind(cap=1)
        second = self.role(P2, "阿雪")
        self.bind(P2, G1, 2)
        one = self.ai.prepare(G1, "小桃和阿雪", "1", first, NOW)
        self.ai.finish(one, RESULT, NOW)
        with self.assertRaisesRegex(ToolError, "本角色在该群"):
            self.ai.prepare(G1, "小桃", "2", first, NOW+6)
        self.assertIsNotNone(self.ai.prepare(G1, "阿雪", "2", second, NOW+6))
        self.ai.select(P1, first)
        self.ai.configure(P1, "limit", "1")
        with self.assertRaisesRegex(ToolError, "每日总额度"):
            self.ai.prepare(P1, "小桃", "3", first, NOW+10)

    def test_role_owner_configures_each_group_trigger_without_exposing_key(self):
        self.role()
        grant = self.bind()
        default = self.ai.group_settings(P1, grant["id"])
        self.assertEqual((default["mention_only"], default["collect_min"], default["collect_max"]), (1, 0, 0))
        text = ai_commands.dispatch(self.ai, P1, "群设置 " + grant["id"])
        self.assertIn("必须提到角色名", text)
        self.assertIn("Token", text)
        self.assertNotIn(KEY, text)
        with self.assertRaisesRegex(ToolError, "属于你的群授权"):
            self.ai.group_settings(P2, grant["id"], "mention", "off")
        with self.assertRaisesRegex(ToolError, "私聊"):
            self.ai.group_settings(G1, grant["id"], "mention", "off")
        self.ai.group_settings(P1, grant["id"], "mention", "off")
        configured = self.ai.group_settings(P1, grant["id"], "collect", "2 4")
        self.assertEqual((configured["mention_only"], configured["collect_min"], configured["collect_max"]), (0, 2, 4))
        for value in ("0 3", "5 3", "1 21", "x y", ""):
            with self.subTest(value=value), self.assertRaises(ToolError):
                self.ai.group_settings(P1, grant["id"], "collect", value)
        warning = ai_commands.dispatch(self.ai, P1, f"群设置 {grant['id']} 收集 0")
        warning = ai_commands.dispatch(self.ai, P1, f"群设置 {grant['id']} 点名 off")
        self.assertIn("每条普通消息", warning)
        self.assertIn("Token", warning)

    def test_group_messages_are_deduplicated_collected_and_batched(self):
        role = self.role()
        grant = self.bind()
        self.ai.group_settings(P1, grant["id"], "mention", "off")
        self.ai.group_settings(P1, grant["id"], "collect", "2 2")
        first = self.ai.collect_targets(G1, "第一句，没有角色名", "m1", NOW)
        self.assertEqual(first, [])
        self.assertEqual(self.ai.collect_targets(G1, "重复投递", "m1", NOW+1), [])
        speaker = replace(G1, user="another-member")
        selected = self.ai.collect_targets(speaker, "第二句，也没有角色名", "m2", NOW+2)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].owner, role)
        self.assertTrue(selected[0].batched)
        self.assertIn("第一句", selected[0].prompt)
        self.assertIn("第二句", selected[0].prompt)
        request = self.ai.prepare(speaker, selected[0].prompt, "m2", role, NOW+2, batched=True)
        self.assertIsNotNone(request)
        self.assertIn("群聊最近消息", request.messages[-1]["content"])
        self.ai.finish(request, RESULT, NOW+2)
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_group_buffer").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_group_seen").fetchone()[0], 2)

    def test_name_rule_and_zero_collection_are_independent(self):
        role = self.role()
        grant = self.bind()
        self.ai.group_settings(P1, grant["id"], "collect", "2 2")
        self.assertEqual(self.ai.collect_targets(G1, "普通消息", "n1", NOW), [])
        self.assertEqual(self.ai.collect_targets(G1, "小桃第一句", "n2", NOW+1), [])
        selected = self.ai.collect_targets(G1, "再次问小桃", "n3", NOW+2)
        self.assertEqual([item.owner for item in selected], [role])
        self.ai.group_settings(P1, grant["id"], "mention", "off")
        self.ai.group_settings(P1, grant["id"], "collect", "0")
        self.assertEqual([item.owner for item in self.ai.collect_targets(G1, "不点名也回复", "n4", NOW+3)], [role])

    def test_named_batch_remains_valid_when_rendering_clips_the_name(self):
        role = self.role()
        grant = self.bind()
        self.ai.group_settings(P1, grant["id"], "collect", "2 2")
        long_named = "很长的聊天内容" * 80 + "小桃"
        self.assertEqual(self.ai.collect_targets(G1, long_named, "long-1", NOW), [])
        selected = self.ai.collect_targets(G1, long_named, "long-2", NOW+1)
        self.assertEqual(len(selected), 1)
        self.assertNotIn("小桃", selected[0].prompt)
        request = self.ai.prepare(
            G1, selected[0].prompt, "long-2", role, NOW+1, batched=True
        )
        self.assertIsNotNone(request)

    def test_legacy_group_schema_gets_trigger_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = Store(temp)
            with legacy.connect() as db:
                db.execute("""CREATE TABLE ai_groups (
                    scope TEXT NOT NULL, bot TEXT NOT NULL, ref TEXT NOT NULL,
                    id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, sponsor TEXT NOT NULL,
                    cap INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(scope,sponsor))""")
                db.execute("""CREATE TABLE ai_profiles (
                    actor TEXT PRIMARY KEY, owner TEXT NOT NULL, bot TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'glm', region TEXT NOT NULL DEFAULT 'cn',
                    secret BLOB, name TEXT NOT NULL DEFAULT '', persona TEXT NOT NULL DEFAULT '',
                    daily INTEGER NOT NULL DEFAULT 100, revision TEXT NOT NULL)""")
            AIStore(legacy)
            with legacy.connect() as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_groups)")}
                profile_columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_profiles)")}
            self.assertTrue({"mention_only", "collect_min", "collect_max", "collect_target"} <= columns)
            self.assertIn("model", profile_columns)

    def test_memory_is_isolated_per_role_group_and_private(self):
        first = self.role()
        self.bind(P1, G1)
        self.bind(P1, G2)
        second = self.role(P2, "阿雪")
        self.bind(P2, G1)
        request = self.ai.prepare(G1, "小桃，这是群一秘密", "1", first, NOW)
        self.ai.finish(request, RESULT, NOW)
        for who, role, text in ((G2, first, "小桃"), (P1, first, "小桃"), (G1, second, "阿雪")):
            request = self.ai.prepare(who, text, who.scope+role, role, NOW+6)
            self.assertNotIn("群一秘密", json.dumps(request.messages, ensure_ascii=False))
            self.assertNotIn(KEY, json.dumps(request.messages))
            self.ai.finish(request, RESULT, NOW+6)
        request = self.ai.prepare(G1, "小桃，再见", "next", first, NOW+12)
        self.assertIn("群一秘密", json.dumps(request.messages, ensure_ascii=False))
        self.assertNotIn(G1.user, json.dumps(request.messages))

    def test_revoke_during_request_does_not_restore_memory_or_affect_other_roles(self):
        first = self.role()
        grant = self.bind()
        second = self.role(P2, "阿雪")
        self.bind(P2, G1)
        request = self.ai.prepare(G1, "小桃", "1", first, NOW)
        self.ai.revoke(P1, grant["id"])
        self.assertFalse(self.ai.finish(request, RESULT, NOW))
        self.assertEqual(self.ai.targets(G1, "小桃和阿雪"), [second])
        self.assertEqual(self.ai.usage(P1, NOW)["input"], 100)

    def test_delete_selected_role_key_leaves_other_roles_and_groups(self):
        first = self.role()
        self.ai.configure(P1, "model", "glm-4.7")
        self.bind(P1, G1)
        second = self.role(P1, "阿雪")
        self.ai.copy_key(P1, first)
        self.assertEqual(self.ai.profile(P1)["model"], "glm-4.7")
        self.bind(P1, G2)
        self.ai.delete_key(P1)
        self.assertEqual(self.ai.targets(G1, "小桃"), [first])
        self.assertEqual(self.ai.targets(G2, "阿雪"), [])
        self.assertTrue(next(r for r in self.ai.roles(P1) if r["id"] == first)["has_key"])
        with self.assertRaises(ToolError):
            self.ai.copy_key(P2, first)

    def test_switching_service_revokes_only_selected_role_and_removes_key(self):
        self.role()
        self.bind()
        self.ai.configure(P1, "service", "qwen singapore")
        self.assertFalse(self.ai.profile(P1)["has_key"])
        self.assertEqual(
            ai_provider.model_for("qwen", "singapore", self.ai.profile(P1)["model"]).code,
            "qwen-flash-character",
        )
        self.assertEqual(self.ai.group(G1), [])
        with self.assertRaises(ToolError):
            self.ai.configure(P1, "service", "qwen https://evil.example")

    def test_manual_model_switch_keeps_key_and_group_but_clears_memory(self):
        role = self.role()
        grant = self.bind()
        request = self.ai.prepare(G1, "小桃你好", "before-model", role, NOW)
        self.ai.finish(request, RESULT, NOW)
        self.ai.configure(P1, "model", "glm-4.7")
        profile = self.ai.profile(P1)
        self.assertEqual(profile["model"], "glm-4.7")
        self.assertTrue(profile["has_key"])
        self.assertEqual(self.ai.groups(P1)[0]["id"], grant["id"])
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_history").fetchone()[0], 0)
        after = self.ai.prepare(G1, "小桃还在吗", "after-model", role, NOW + 6)
        self.assertEqual(after.model, "glm-4.7")
        self.assertEqual(after.key, KEY)
        self.assertEqual(len(after.messages), 2)
        with self.assertRaisesRegex(ToolError, "可选列表"):
            self.ai.configure(P1, "model", "qwen3.7-plus")

    def test_model_command_lists_default_recommendation_and_reason(self):
        self.role()
        listing = ai_commands.dispatch(self.ai, P1, "模型")
        self.assertIn("默认推荐", listing)
        self.assertIn("glm-4.7-flash", listing)
        self.assertIn("日常角色聊天性价比最高", listing)
        changed = ai_commands.dispatch(self.ai, P1, "模型 glm-4.7")
        self.assertIn("模型已切换", changed)
        self.assertIn("不是默认推荐", changed)
        status = ai_commands.dispatch(self.ai, P1, "状态")
        self.assertIn("glm-4.7", status)
        self.assertIn("选择理由", status)
        self.assertLessEqual(len(listing.encode()), 1800)

    def test_qwen_model_list_has_one_default_recommendation_and_reason(self):
        self.role()
        ai_commands.dispatch(self.ai, P1, "服务 qwen beijing")
        listing = ai_commands.dispatch(self.ai, P1, "模型")
        self.assertIn("qwen-flash-character-2026-02-26（默认推荐、当前）", listing)
        self.assertIn("专为角色扮演优化", listing)
        self.assertIn("qwen3.7-plus", listing)
        self.assertEqual(listing.count("默认推荐"), 1)
        self.assertLessEqual(len(listing.encode()), 1800)

    def test_reselecting_same_service_does_not_mislabel_custom_model(self):
        self.role()
        ai_commands.dispatch(self.ai, P1, "模型 glm-4.7")
        result = ai_commands.dispatch(self.ai, P1, "服务 glm")
        self.assertIn("当前模型：glm-4.7", result)
        self.assertNotIn("默认推荐：glm-4.7", result)

    def test_moderation_uses_current_group_role_and_never_exposes_key(self):
        role = self.role()
        grant = self.bind()
        with self.assertRaises(ToolError):
            self.ai.moderate(G1, "pause", grant["id"])
        admin = replace(G1, group_role="admin")
        self.ai.moderate(admin, "pause", grant["id"])
        self.assertEqual(self.ai.targets(G1, "小桃"), [])
        self.ai.moderate(admin, "resume", grant["id"])
        self.assertEqual(self.ai.targets(G1, "小桃"), [role])
        self.ai.moderate(admin, "remove", grant["id"])
        self.assertEqual(self.ai.group(G1), [])
        grant = self.bind()
        with self.assertRaisesRegex(ToolError, "请指定角色编号"):
            self.ai.moderate(admin, "remove", "all")
        with self.assertRaises(ToolError):
            self.ai.moderate(replace(G2, group_role="owner"), "remove", grant["id"])
        self.ai.moderate(replace(G1, group_role="owner"), "remove", "all")
        self.assertEqual(self.ai.group(G1), [])

    def test_recognized_bot_owner_can_remove_all_roles_in_current_group(self):
        self.role()
        self.bind()
        root = replace(G1, user="root", group_role="member")
        self.base.register(root)
        with self.base.connect() as db:
            db.execute("UPDATE identities SET role='owner' WHERE actor=?", (root.actor,))
        self.ai.moderate(root, "remove", "all")
        self.assertEqual(self.ai.group(G1), [])

    def test_global_switch_requires_server_authorized_private_owner(self):
        self.role()
        with self.assertRaises(ToolError):
            self.ai.global_switch(P1, False)
        with self.assertRaises(ToolError):
            self.ai.global_switch(replace(G1, group_role="owner"), False)

    def test_ten_members_can_each_add_one_role_to_same_group(self):
        expected = set()
        for index in range(10):
            who = Identity("test", f"private:u{index}", f"u{index}", True)
            expected.add(self.role(who, f"角色{index}"))
            self.bind(who, G1)
        self.assertEqual(len(self.ai.group(G1)), 10)
        self.assertEqual(set(self.ai.targets(G1, " ".join(f"角色{i}" for i in range(10)))), expected)
        self.assertIn("第 2 页", ai_commands.dispatch(self.ai, G1, "状态 2"))

    def test_config_and_other_owned_role_secrets_never_enter_prompts(self):
        first = self.role()
        second = self.role(P1, "阿雪", "another-fake-only-key.12345678")
        self.assertEqual(self.ai.targets(P1, "小桃，帮我看 /ai 密钥 " + KEY), [])
        self.assertIsNone(self.ai.prepare(P1, "阿雪 /bot whoami " + "a"*32, "1", second, NOW))
        with self.assertRaises(ToolError):
            self.ai.prepare(P1, "阿雪 " + KEY, "2", second, NOW)
        with self.assertRaises(ToolError):
            self.ai.create_role(P1, "小雨 | " + KEY)
        self.assertEqual(self.ai.usage(P1, NOW)["total"], 0)

    def test_history_bounded_and_clear_during_request_cancels_reply(self):
        first = self.role()
        self.bind(cap=10)
        for index in range(5):
            request = self.ai.prepare(G1, "小桃你好" + str(index), str(index), first, NOW+index*6)
            self.ai.finish(request, RESULT, NOW+index*6)
        with self.base.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_history").fetchone()[0], 3)
        request = self.ai.prepare(G1, "小桃", "clear", first, NOW+36)
        self.ai.moderate(replace(G1, group_role="admin"), "clear", "all")
        self.assertFalse(self.ai.finish(request, RESULT, NOW+36))
        fresh = self.ai.prepare(G1, "小桃", "fresh", first, NOW+42)
        self.assertEqual(len(fresh.messages), 2)

    def test_delete_recreate_cannot_reset_user_budget(self):
        role = self.role()
        self.ai.configure(P1, "limit", "1")
        request = self.ai.prepare(P1, "小桃", "1", role, NOW)
        self.ai.finish(request, RESULT, NOW)
        self.ai.delete_role(P1, role)
        role = self.role(P1, "阿雪")
        with self.assertRaisesRegex(ToolError, "每日总额度"):
            self.ai.prepare(P1, "阿雪", "2", role, NOW+6)

    def test_new_commands_edit_and_delete_only_selected_role(self):
        first = self.role()
        second = self.role(P1, "阿雪")
        ai_commands.dispatch(self.ai, P1, "选择 " + first)
        ai_commands.dispatch(self.ai, P1, "角色 小雨 | 开朗")
        self.assertEqual(self.ai.profile(P1)["name"], "小雨")
        ai_commands.dispatch(self.ai, P1, "删除角色 " + first + " 确认")
        self.assertEqual(self.ai.profile(P1)["id"], second)
        self.assertTrue(self.ai.profile(P1)["has_key"])

    def test_redaction_survives_real_nonebot_initialization_in_fresh_process(self):
        root = Path(__file__).resolve().parents[1]
        script = "\n".join([
            "from bot_tools.security import initialize_nonebot_safely",
            "from nonebot.log import logger",
            "initialize_nonebot_safely(_env_file=('nonexistent-test-file.env',), driver='~fastapi+~httpx', qq_bots=[], log_level='DEBUG', test_secret='INIT-SECRET-SHOULD-NOT-LOG')",
            "logger.info('/ai 密钥 fake-test-only-credential.12345678')",
            "logger.info('masking-smoke-ok')",
        ])
        environment = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-c", script], cwd=self.temp.name, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("masking-smoke-ok", result.stdout)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertNotIn("INIT-SECRET-SHOULD-NOT-LOG", result.stdout + result.stderr)


class AIProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_every_provider_default_matches_exactly_one_recommended_model(self):
        for key, provider in ai_provider.PROVIDERS.items():
            recommended = [option for option in ai_provider.MODELS[key] if option.recommended]
            self.assertEqual(len(recommended), 1)
            self.assertEqual(provider.model, recommended[0].code)

    async def test_glm_and_qwen_payloads_and_regional_allowlist(self):
        for provider, region in ai_provider.PROVIDERS:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={"choices": [{"message": {"content": "你好", "reasoning_content": "not visible"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
            result = await ai_provider.complete(provider, region, KEY, [{"role":"user", "content":"test"}], transport=httpx.MockTransport(handler))
            self.assertEqual(result, ai_provider.Completion("你好", 5, 2, True))
            self.assertEqual(str(seen[0].url), ai_provider.PROVIDERS[provider, region].url)
            self.assertEqual(seen[0].headers["authorization"], "Bearer " + KEY)
            body = json.loads(seen[0].content)
            self.assertEqual(body["max_tokens"], 256)
            self.assertEqual(body["model"], ai_provider.model_for(provider, region).code)
            self.assertEqual(body["stream"], provider == "glm")
            self.assertEqual(seen[0].extensions["timeout"]["read"], 90 if provider == "glm" else 35)
            self.assertNotIn("tools", body)
            self.assertEqual(body.get("thinking"), {"type": "disabled"} if provider == "glm" else None)
        with self.assertRaises(ToolError):
            await ai_provider.complete("qwen", "evil", KEY, [])

    async def test_selected_models_change_payload_without_changing_endpoint(self):
        cases = (("glm", "cn", "glm-5.2"), ("qwen", "beijing", "qwen3.7-plus"))
        for provider, region, model in cases:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})
            await ai_provider.complete(
                provider, region, KEY, [], model=model, transport=httpx.MockTransport(handler)
            )
            body = json.loads(seen[0].content)
            self.assertEqual(body["model"], model)
            self.assertEqual(str(seen[0].url), ai_provider.provider_for(provider, region).url)
            self.assertEqual(body.get("enable_thinking"), False if provider == "qwen" else None)
        with self.assertRaisesRegex(ToolError, "可选列表"):
            await ai_provider.complete("qwen", "beijing", KEY, [], model="qwen-math-turbo")

    async def test_qwen_free_tier_exhaustion_has_manual_switch_guidance(self):
        def handler(request):
            return httpx.Response(403, json={"code": "AllocationQuota.FreeTierOnly", "message": KEY})
        with self.assertRaisesRegex(ToolError, "/ai 模型") as caught:
            await ai_provider.complete(
                "qwen", "beijing", KEY, [], transport=httpx.MockTransport(handler)
            )
        self.assertIn("免费 Token 已用完", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    async def test_errors_redirects_and_secrets_are_not_relayed_or_retried(self):
        for status in (302, 400, 401, 403, 402, 429, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, text=KEY, headers={"Location":"https://evil.example"})
            with self.assertRaises(ToolError) as error:
                await ai_provider.complete("glm", "cn", KEY, [], transport=httpx.MockTransport(handler))
            self.assertNotIn(KEY, str(error.exception))
            self.assertEqual(len(calls), 1)

    async def test_oversized_malformed_and_empty_results(self):
        for data in (b"x"*140000, b"not-json", b'{"choices":[]}', b'{"choices":[{"message":{"content":""}}]}'):
            with self.assertRaises(ToolError):
                await ai_provider.complete("glm", "cn", KEY, [], transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data)))


def event(private: bool, text: str, number="1", user="one", role="member", bot_author=False):
    data = {"id":number, "timestamp":"2026-09-04T00:00:00Z", "content":text, "to_me":private}
    if private:
        return C2CMessageCreateEvent.model_validate({**data, "author":{"id":user,"user_openid":user}})
    return GroupMessageCreateEvent.model_validate({**data, "group_id":"one", "group_openid":"one",
        "author":{"id":user,"member_openid":user,"member_role":role,"bot":bot_author}})


class AIEntryTests(AIFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.init_store()
        adapter = MagicMock()
        adapter.config = nonebot.get_driver().config
        adapter.get_name.return_value = "QQ"
        self.bot = Bot(adapter, "test", BotInfo(id="test", token="", secret="test-only"))
        self.bot.send = AsyncMock(return_value={})
        for target, attr, value in ((ai_chat,"get_ai",self.ai),(toolbox,"get_store",self.base)):
            patcher = patch.object(target, attr, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_real_entrypoint_member_creates_multiple_roles_without_privilege(self):
        with patch.object(self.base, "throttle"):
            await handle_event(self.bot, event(True, "/ai 新建 小桃 | 可爱的角色", "1"))
            await handle_event(self.bot, event(True, "/ai 新建 阿雪 | 活泼的角色", "2"))
        self.assertEqual(len(self.ai.roles(P1)), 2)
        self.assertEqual(self.bot.send.await_count, 2)
        self.assertFalse(self.base.is_admin(P1))

    async def test_real_group_event_multiple_roles_and_no_at_or_recursion(self):
        self.role(P1, "小桃")
        self.ai.configure(P1, "model", "glm-4.7")
        self.bind(P1, G1)
        self.role(P2, "阿雪")
        self.bind(P2, G1)
        with patch.object(ai_provider, "complete", new_callable=AsyncMock, return_value=RESULT) as complete:
            await handle_event(self.bot, event(False, "大家问问小桃还有阿雪吧", "names"))
            self.assertEqual(complete.await_count, 2)
            self.assertEqual(self.bot.send.await_count, 2)
            self.assertEqual(
                {call.kwargs["model"] for call in complete.await_args_list},
                {"glm-4.7", "glm-4.7-flash"},
            )
            await handle_event(self.bot, event(False, "大家问问小桃还有阿雪吧", "names"))
            await handle_event(self.bot, event(False, "普通消息", "normal"))
            await handle_event(self.bot, event(False, "小桃阿雪", "bot", bot_author=True))
            self.assertEqual(complete.await_count, 2)

    async def test_real_group_event_can_batch_without_name_after_owner_configures_it(self):
        self.role(P1, "小桃")
        grant = self.bind(P1, G1)
        self.ai.group_settings(P1, grant["id"], "mention", "off")
        self.ai.group_settings(P1, grant["id"], "collect", "2 2")
        with patch.object(ai_provider, "complete", new_callable=AsyncMock, return_value=RESULT) as complete:
            await handle_event(self.bot, event(False, "第一条普通群消息", "batch-1", user="one"))
            complete.assert_not_awaited()
            self.bot.send.assert_not_awaited()
            await handle_event(self.bot, event(False, "第二条普通群消息", "batch-2", user="two"))
            complete.assert_awaited_once()
            self.bot.send.assert_awaited_once()
            messages = complete.await_args.args[3]
            prompt = messages[-1]["content"]
            self.assertIn("第一条普通群消息", prompt)
            self.assertIn("第二条普通群消息", prompt)

    async def test_secret_commands_are_redacted_before_event_logs_and_never_call_ai(self):
        self.role()
        output = io.StringIO()
        logger.configure(patcher=redact_binding_log)
        sink = logger.add(output, level="DEBUG", format="{message}")
        try:
            with patch.object(self.base, "throttle"), patch.object(ai_provider, "complete", new_callable=AsyncMock) as complete:
                await handle_event(self.bot, event(True, "/ai 密钥 " + KEY))
                await handle_event(self.bot, event(False, "/ai 密钥 " + KEY, "group"))
                complete.assert_not_awaited()
            self.assertNotIn(KEY, output.getvalue())
            self.assertIn("已隐藏", output.getvalue())
            self.assertTrue(all(KEY not in str(call.args[1]) for call in self.bot.send.call_args_list))
        finally:
            logger.remove(sink)
            logger.configure(patcher=None)

    async def test_disabled_ai_still_allows_deleting_own_key(self):
        self.role()
        with self.base.state(P1.scope_key) as doc:
            doc["disabled"] = ["ai"]
        with patch.object(self.base, "throttle"):
            await handle_event(self.bot, event(True, "/ai 删除密钥 确认"))
        self.assertFalse(self.ai.profile(P1)["has_key"])
