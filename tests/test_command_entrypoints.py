from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nonebot
from PIL import Image

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi+~httpx+~websockets")

from bot_tools.storage import Identity, Store, ToolError
from bot_tools.catalog import ff14_directory
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent, GroupMessageCreateEvent
from nonebot.message import handle_event
from plugins import toolbox


def event_for(scene: str, content: str, *, role: str = "owner", author_bot: bool = False,
              attachments: list[dict] | None = None, group: str = "group-a"):
    data = {"id":"test-command", "timestamp":"2026-09-04T00:00:00Z", "content":content}
    if attachments is not None:
        data["attachments"] = attachments
    if scene == "private":
        return C2CMessageCreateEvent.model_validate({
            **data, "to_me":True, "author":{"id":"member", "user_openid":"member"},
        })
    cls = GroupAtMessageCreateEvent if scene == "at" else GroupMessageCreateEvent
    return cls.model_validate({
        **data, "to_me":scene == "at", "group_id":group, "group_openid":group,
        "author":{"id":"member", "member_openid":"member", "member_role":role, "bot":author_bot},
    })


class CommandEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(temp.name)
        adapter = MagicMock()
        adapter.config = nonebot.get_driver().config
        adapter.get_name.return_value = "QQ"
        self.bot = Bot(adapter, "test", BotInfo(id="test", token="", secret="test-only"))
        self.bot.send = AsyncMock(return_value={})
        patcher = patch.object(toolbox, "get_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reply(self) -> str:
        call = self.bot.send.call_args
        return str(call.kwargs["message"] if "message" in call.kwargs else call.args[1])

    async def test_cat_without_at_reaches_handler_and_sends_image(self):
        output = io.BytesIO()
        Image.new("RGB", (20,20), "orange").save(output, "PNG")
        with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock, return_value=output.getvalue()) as fetch:
            await handle_event(self.bot, event_for("full", "/cat", role="member"))
            fetch.assert_awaited_once()
        self.assertEqual(self.bot.send.call_count, 1)
        self.assertIn("file_image", self.reply())

    async def test_members_can_upload_in_full_at_and_private_chats_without_binding(self):
        output = io.BytesIO()
        Image.new("RGB", (20,20), "orange").save(output, "PNG")
        attachment = {"url":"https://gchat.qpic.cn/test.png", "content_type":"image/png"}
        for scene in ("full", "at", "private"):
            with self.subTest(scene=scene), patch.object(self.store, "throttle"):
                self.bot.send.reset_mock()
                event = event_for(scene, "/image add CAT", role="member", attachments=[attachment])
                who = toolbox.identity(self.bot, event)
                with patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock,
                                  return_value=output.getvalue()) as fetch:
                    await handle_event(self.bot, event)
                    fetch.assert_awaited_once_with(attachment["url"])
                self.assertEqual(self.bot.send.call_count, 1)
                self.assertIn("图片已入库", self.reply())
                self.assertIn("分类 cat", self.reply())
                self.assertIsNotNone(toolbox.media.gallery_get(self.store, who, "cat"))
                self.assertFalse(self.store.is_admin(who))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM identities").fetchone()[0], 0)
            # Full-message, @ and private events all use the same bot-wide
            # public gallery, so identical uploads are de-duplicated.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gallery").fetchone()[0], 1)

    async def test_member_cannot_delete_even_own_upload_but_group_admin_can(self):
        output = io.BytesIO()
        Image.new("RGB", (20,20), "orange").save(output, "PNG")
        owner = toolbox.identity(self.bot, event_for("full", "", role="owner"))
        toolbox.community.management(self.store, owner, "group", "gallery local")
        who = toolbox.identity(self.bot, event_for("full", "/image", role="member"))
        item_id = toolbox.media.gallery_add(self.store, who, "cat", output.getvalue())
        for role in ("member", "admin"):
            with self.subTest(role=role), patch.object(self.store, "throttle"):
                self.bot.send.reset_mock()
                await handle_event(self.bot, event_for("full", f"/image del {item_id}", role=role))
                self.assertEqual(self.bot.send.call_count, 1)
                if role == "member":
                    self.assertIn("没有完成", self.reply())
                    self.assertIsNotNone(toolbox.media.gallery_get(self.store, who, "cat"))
                else:
                    self.assertIn("图片已移除", self.reply())
                    self.assertIsNone(toolbox.media.gallery_get(self.store, who, "cat"))

    async def test_group_owner_switch_and_public_gif_round_trip_between_groups(self):
        output = io.BytesIO()
        frames = [Image.new("RGB", (20,20), color) for color in ("red", "green", "blue")]
        frames[0].save(output, "GIF", save_all=True, append_images=frames[1:], duration=[80,160,240], loop=2)
        original = output.getvalue()
        with patch.object(self.store, "throttle"):
            for group in ("group-a", "group-b"):
                self.bot.send.reset_mock()
                await handle_event(self.bot, event_for("full", "/group gallery public", role="owner", group=group))
                self.assertIn("图库模式已切换", self.reply())
            attachment = {"url":"https://gchat.qpic.cn/test.gif", "content_type":"image/gif"}
            with patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock, return_value=original):
                await handle_event(self.bot, event_for("full", "/image add cat", role="member", attachments=[attachment]))
            self.assertIn("公共图库", self.reply())
            self.assertIn("GIF 原文件完整保留", self.reply())
            for command in ("/image cat", "/cat"):
                self.bot.send.reset_mock()
                with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock) as online:
                    await handle_event(self.bot, event_for("full", command, role="member", group="group-b"))
                    online.assert_not_awaited()
                self.assertEqual(self.bot.send.call_count, 1)
                call = self.bot.send.call_args
                message = call.kwargs["message"] if "message" in call.kwargs else call.args[1]
                segment = message if hasattr(message, "data") else message[0]
                self.assertEqual(segment.data["file_name"], "gallery.gif")
                self.assertEqual(segment.data["content"], original)
            await handle_event(self.bot, event_for("full", "/group gallery local", role="owner", group="group-b"))
            await handle_event(self.bot, event_for("full", "/image cat", role="member", group="group-b"))
            self.assertIn("分类为空", self.reply())

    async def test_group_admin_cannot_switch_gallery_via_real_command(self):
        for role in ("admin", "member"):
            with self.subTest(role=role), patch.object(self.store, "throttle"):
                self.bot.send.reset_mock()
                event = event_for("full", "/group gallery public", role=role)
                await handle_event(self.bot, event)
                self.assertIn("没有完成", self.reply())
                self.assertTrue(toolbox.gallery.current(self.store, toolbox.identity(self.bot, event)).public)

    async def test_switch_during_image_download_does_not_publish_private_upload(self):
        owner = toolbox.identity(self.bot, event_for("full", "", role="owner"))
        toolbox.community.management(self.store, owner, "group", "gallery local")

        async def downloading(*args):
            who = toolbox.identity(self.bot, event_for("full", "", role="owner"))
            toolbox.community.management(self.store, who, "group", "gallery public")
            output = io.BytesIO()
            Image.new("RGB", (20,20), "red").save(output, "PNG")
            return output.getvalue()
        attachment = {"url":"https://gchat.qpic.cn/test.png", "content_type":"image/png"}
        with patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock, side_effect=downloading):
            await handle_event(self.bot, event_for("full", "/image add cat", role="member", attachments=[attachment]))
        self.assertIn("图库模式已改变", self.reply())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gallery").fetchone()[0], 0)

    async def test_upload_rejects_invalid_arguments_and_attachments_before_fetch(self):
        attachment = {"url":"https://gchat.qpic.cn/test.png", "content_type":"image/png"}
        cases = [
            ("/image add", [attachment], "用法"),
            ("/image add ../../data", [attachment], "分类限"),
            ("/image add cat", [], "附上 1～10 张图片"),
            ("/image add cat", [attachment]*11, "最多上传 10 张"),
            ("/image add cat", [{**attachment,"content_type":"text/plain"}], "只能包含直接上传的 QQ 图片"),
            ("/image add cat", [{"content_type":"image/png"}], "只能包含直接上传的 QQ 图片"),
        ]
        for command, attachments, expected in cases:
            with self.subTest(command=command, attachments=attachments), patch.object(self.store, "throttle"):
                self.bot.send.reset_mock()
                with patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock) as fetch:
                    await handle_event(self.bot, event_for("full", command, role="member", attachments=attachments))
                    fetch.assert_not_awaited()
                self.assertIn(expected, self.reply())

    async def test_members_can_upload_multiple_images_with_one_command(self):
        images = []
        for color in ("red", "blue"):
            output = io.BytesIO()
            Image.new("RGB", (20,20), color).save(output, "PNG")
            images.append(output.getvalue())
        attachments = [
            {"url":f"https://gchat.qpic.cn/test-{index}.png", "content_type":"image/png"}
            for index in range(2)
        ]
        with patch.object(self.store, "throttle"), \
             patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock,
                          side_effect=images) as fetch:
            await handle_event(self.bot, event_for(
                "full", "/image add cat", role="member", attachments=attachments))
        self.assertEqual(fetch.await_count, 2)
        self.assertIn("批量图片上传完成", self.reply())
        self.assertIn("成功 2/2 张", self.reply())
        self.assertIn("第 1 张：已入库", self.reply())
        self.assertIn("第 2 张：已入库", self.reply())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gallery").fetchone()[0], 2)

    async def test_batch_upload_reports_one_failure_without_losing_successes(self):
        output = io.BytesIO()
        Image.new("RGB", (20,20), "red").save(output, "PNG")
        attachments = [
            {"url":f"https://gchat.qpic.cn/test-{index}.png", "content_type":"image/png"}
            for index in range(2)
        ]
        with patch.object(self.store, "throttle"), \
             patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock,
                          side_effect=[output.getvalue(), ToolError("图片下载失败")]):
            await handle_event(self.bot, event_for(
                "full", "/image add cat", role="member", attachments=attachments))
        self.assertIn("成功 1/2 张", self.reply())
        self.assertIn("第 2 张：失败 · 图片下载失败", self.reply())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gallery").fetchone()[0], 1)

    async def test_upload_switch_quota_and_cooldown_still_block_download(self):
        attachment = {"url":"https://gchat.qpic.cn/test.png", "content_type":"image/png"}
        for guard in ("switch", "quota", "cooldown"):
            with self.subTest(guard=guard):
                self.bot.send.reset_mock()
                event = event_for("full", "/image add cat", role="member", attachments=[attachment])
                who = toolbox.identity(self.bot, event)
                with self.store.state(who.scope_key) as doc:
                    doc["disabled"] = ["image"] if guard == "switch" else []
                    doc["quota"] = 100
                    if guard == "quota":
                        doc["quota"] = 1
                        toolbox.community.quota(doc, who.actor, True)
                # Independent cooldown fixture; the other two gates use the real store logic.
                cooldown = ToolError("操作太快") if guard == "cooldown" else None
                with patch.object(self.store, "throttle", side_effect=cooldown), \
                     patch.object(toolbox.media, "fetch_image", new_callable=AsyncMock) as fetch:
                    await handle_event(self.bot, event)
                    fetch.assert_not_awaited()
                self.assertIn({"switch":"已关闭 /image", "quota":"次数已用完", "cooldown":"操作太快"}[guard], self.reply())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gallery").fetchone()[0], 0)

    async def test_every_new_command_help_replies_in_full_at_and_private_chats(self):
        who = toolbox.identity(self.bot, event_for("private", "/bot"))
        record = self.store.redeem_admin_token(who, self.store.create_admin_token())
        self.store.grant(record["code"], "owner")
        for scene in ("full", "at", "private"):
            for command in sorted(toolbox.COMMANDS | {"toolbox"}):
                with self.subTest(scene=scene, command=command):
                    self.bot.send.reset_mock()
                    await handle_event(self.bot, event_for(scene, f"/{command} help"))
                    self.assertEqual(self.bot.send.call_count, 1)
                    reply = self.reply()
                    self.assertTrue(reply.strip())
                    self.assertNotIn("工具暂时未完成", reply)
                    self.assertNotIn("无权访问", reply)
                    self.assertLessEqual(len(reply.encode()), 1800)

    async def test_cat_network_failure_returns_clear_error_without_at(self):
        with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock, side_effect=ToolError("猫图服务暂时不可用")):
            await handle_event(self.bot, event_for("full", "/cat", role="member"))
        self.assertEqual(self.bot.send.call_count, 1)
        self.assertIn("猫图服务暂时不可用", self.reply())

    async def test_cat_upload_rejection_returns_text_error_without_at(self):
        output = io.BytesIO()
        Image.new("RGB", (20,20), "orange").save(output, "PNG")
        self.bot.send.side_effect = [RuntimeError("test QQ media rejection"), {}]
        with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock, return_value=output.getvalue()):
            await handle_event(self.bot, event_for("full", "/cat", role="member"))
        self.assertEqual(self.bot.send.call_count, 2)
        self.assertIn("QQ 未接受发送", self.reply())

    async def test_disabled_command_still_denied_without_at(self):
        who = toolbox.identity(self.bot, event_for("full", "/command", role="owner"))
        toolbox.community.management(self.store, who, "command", "disable cat")
        with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock) as fetch:
            await handle_event(self.bot, event_for("full", "/cat", role="member"))
            fetch.assert_not_awaited()
        self.assertEqual(self.bot.send.call_count, 1)
        self.assertIn("已关闭 /cat", self.reply())

    async def test_ff14_umbrella_is_enforced_by_group_message_preprocessor(self):
        from plugins import ffxiv_tools  # Register FF14 matchers in isolated runs.

        self.assertIsNotNone(ffxiv_tools.ff14)
        owner = toolbox.identity(self.bot, event_for("full", "/command", role="owner", group="group-a"))
        toolbox.community.management(self.store, owner, "command", "disable ff14")
        for command in ("/ff14", "/gather"):
            with self.subTest(command=command):
                self.bot.send.reset_mock()
                await handle_event(self.bot, event_for("full", command, role="member", group="group-a"))
                self.assertEqual(self.bot.send.call_count, 1)
                self.assertIn("FF14 插件", self.reply())
        self.bot.send.reset_mock()
        await handle_event(self.bot, event_for("full", "/ff14", role="member", group="group-b"))
        self.assertEqual(self.reply(), ff14_directory())

    async def test_member_management_and_group_status_still_denied_without_at(self):
        for command, role in (("/bot", "member"), ("/group quota 1", "member"),
                              ("/custom_reply set 关键词 | 回复", "member"), ("/bot status", "owner")):
            with self.subTest(command=command, role=role), patch.object(self.store, "throttle"):
                self.bot.send.reset_mock()
                await handle_event(self.bot, event_for("full", command, role=role))
                self.assertEqual(self.bot.send.call_count, 1)
                self.assertIn("没有完成", self.reply())
                self.assertNotIn("已保存", self.reply())

    async def test_cooldown_remains_effective_without_at(self):
        who = toolbox.identity(self.bot, event_for("full", "/cat", role="member"))
        self.store.throttle(who.actor)
        with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock) as fetch:
            await handle_event(self.bot, event_for("full", "/cat", role="member"))
            fetch.assert_not_awaited()
        self.assertIn("操作太快", self.reply())

    async def test_non_commands_and_bot_authored_messages_do_not_trigger_tools(self):
        for text, author_bot in (("我想看 /cat", False), ("/catapult", False), ("/cat", True)):
            with self.subTest(text=text, author_bot=author_bot):
                self.bot.send.reset_mock()
                with patch.object(toolbox.media, "cat_picture", new_callable=AsyncMock) as fetch:
                    await handle_event(self.bot, event_for("full", text, author_bot=author_bot))
                    fetch.assert_not_awaited()
                self.bot.send.assert_not_awaited()

    def test_unaddressed_channel_not_treated_as_ordinary_qq_group(self):
        for directed in (False, True):
            channel = SimpleNamespace(author=SimpleNamespace(bot=False), is_tome=lambda: directed)
            self.assertEqual(toolbox.command_eligible(channel), directed)
        self.assertTrue(toolbox.command_eligible(event_for("private", "/cat")))
        self.assertTrue(toolbox.command_eligible(event_for("full", "/cat")))

    def test_shared_directory_covers_every_public_feature(self):
        from plugins import otterbot

        text = toolbox.toolbox_help()
        self.assertEqual(text, otterbot.OTTER_HELP)
        listed = set(re.findall(r"/([a-z0-9_]+)\b", text))
        plugin_text = ff14_directory()
        plugin_listed = set(re.findall(r"/([a-z0-9_]+)\b", plugin_text))
        self.assertTrue(toolbox.COMMANDS <= (listed | plugin_listed))
        old = {"market", "sales", "gather", "recip", "craftcost", "cheapest", "quest", "search", "weather", "house", "fflogs", "dps", "raid", "luck", "random", "dice", "gate", "ping", "about"}
        self.assertTrue(old <= (listed | plugin_listed))
        self.assertIn("ff14", listed)
        self.assertFalse({"market", "gather", "dps", "raid", "fsx", "ofish"} & listed)
        self.assertLessEqual(len(text.encode()), 1800)
        self.assertLessEqual(len(plugin_text.encode()), 1800)
        self.assertNotIn("/bot status", text)
        self.assertNotIn("admin add", text)

    async def test_otter_and_toolbox_entrypoints_return_same_directory(self):
        from plugins import otterbot

        replies = []
        for command in ("/otter", "/otter help", "/toolbox", "/toolbox help"):
            self.bot.send.reset_mock()
            await handle_event(self.bot, event_for("full", command, role="member"))
            self.assertEqual(self.bot.send.call_count, 1)
            replies.append(self.reply())
        self.assertEqual(len(set(replies)), 1)
        self.assertEqual(replies[0], otterbot.OTTER_HELP)
        self.assertIn("/cat", replies[0])
        self.assertIn("/ff14", replies[0])

    async def test_ff14_plugin_entry_works_in_all_message_scenes(self):
        from plugins import ffxiv_tools  # Register the plugin entry in isolated runs.

        self.assertIsNotNone(ffxiv_tools.ff14)
        for scene in ("full", "at", "private"):
            for command in ("/ff14", "/ff14 help"):
                self.bot.send.reset_mock()
                await handle_event(self.bot, event_for(scene, command, role="member"))
                self.assertEqual(self.bot.send.call_count, 1)
                self.assertEqual(self.reply(), ff14_directory())
                self.assertIn("/market", self.reply())
                self.assertIn("/dps", self.reply())
                self.assertNotIn("/cat", self.reply())

    async def test_ff14_plugin_entry_does_not_pretend_to_execute_unknown_queries(self):
        from plugins import ffxiv_tools

        self.assertIsNotNone(ffxiv_tools.ff14)
        await handle_event(self.bot, event_for("full", "/ff14 水晶"))
        self.assertIn("插件查询目录", self.reply())
        self.assertIn("具体查询命令", self.reply())

    async def test_trickcal_board_json_can_be_imported_in_group_for_only_the_sender(self):
        from qbot_trickcal import trickcal_board
        from plugins import trickcal as trickcal_plugin

        document = {
            "version": 1, "units": [[10016, 3]], "cards": [], "pets": [],
            "boards": [[10016, {"selectedNodes": [100], "plannedNodes": [101]}]],
            "filter": [], "stepFilter": [], "statFilter": [],
            "purpleWeight": 8, "goldWeight": 1000,
        }
        raw = json.dumps(document, separators=(",", ":"))
        with patch.object(trickcal_plugin, "get_store", return_value=self.store), \
             patch.object(self.store, "throttle"):
            await handle_event(self.bot, event_for("full", "/tr 蜡笔板 导入 " + raw, role="member"))
        self.assertEqual(self.bot.send.call_count, 1)
        self.assertIn("蜡笔板已导入", self.reply())

        sender = toolbox.identity(self.bot, event_for("private", "", role="member"))
        other = Identity("test", "group:group-a", "other", False)
        board_store = trickcal_board.BoardStore(self.store)
        self.assertIsNotNone(board_store.get(sender))
        self.assertIsNone(board_store.get(other))

    async def test_trickcal_character_combines_images_text_and_random_button(self):
        from plugins import trickcal as trickcal_plugin

        reply = "🍞 嘟嘟脸 · 测试角色\n角色资料"
        output = io.BytesIO()
        Image.new("RGB", (720, 1000), "white").save(output, "JPEG")
        card = output.getvalue()
        image_url = (
            "https://qqbot-file-upload-1251316161.cos.accelerate.myqcloud.com/"
            "card.jpg?signature=test"
        )
        with patch.object(trickcal_plugin, "get_store", return_value=self.store), \
             patch.object(self.store, "throttle"), \
             patch.object(trickcal_plugin.service, "dispatch", new_callable=AsyncMock,
                          return_value=reply), \
             patch.object(trickcal_plugin.service, "character_card", new_callable=AsyncMock,
                          return_value=card) as character_card, \
             patch.object(trickcal_plugin.qq_image_upload, "public_image_url",
                          new_callable=AsyncMock, return_value=image_url) as upload:
            await handle_event(
                self.bot, event_for("full", "/tr 随机角色", role="member")
            )

        self.assertEqual(self.bot.send.call_count, 1)
        action_message = self.bot.send.call_args.args[1]
        self.assertEqual(
            [segment.type for segment in action_message], ["markdown", "keyboard"]
        )
        markdown = action_message["markdown"][0].data["markdown"].content
        self.assertIn("![嘟嘟脸角色长卡 #720px #1000px]", markdown)
        self.assertIn(image_url, markdown)
        character_card.assert_awaited_once_with("随机角色", result=reply)
        upload.assert_awaited_once()
        keyboard = action_message["keyboard"][0].data["keyboard"]
        button = keyboard.content.rows[0].buttons[0]
        self.assertEqual(button.render_data.label, "🎲 随机角色")
        self.assertEqual(button.action.data, "/tr 随机角色 国服")

    async def test_trickcal_character_never_sends_a_detached_random_button(self):
        from plugins import trickcal as trickcal_plugin

        reply = "🍞 嘟嘟脸 · 测试角色\n角色资料"
        output = io.BytesIO()
        Image.new("RGB", (720, 1000), "white").save(output, "JPEG")
        with patch.object(trickcal_plugin, "get_store", return_value=self.store), \
             patch.object(self.store, "throttle"), \
             patch.object(trickcal_plugin.service, "dispatch", new_callable=AsyncMock,
                          return_value=reply), \
             patch.object(trickcal_plugin.service, "character_card", new_callable=AsyncMock,
                          return_value=output.getvalue()), \
             patch.object(trickcal_plugin.qq_image_upload, "public_image_url",
                          new_callable=AsyncMock,
                          side_effect=RuntimeError("QQ upload rejected")):
            await handle_event(
                self.bot, event_for("full", "/tr 角色 测试角色", role="member")
            )

        self.assertEqual(self.bot.send.call_count, 1)
        fallback = self.bot.send.call_args.args[1]
        self.assertEqual([segment.type for segment in fallback], ["markdown", "keyboard"])
        self.assertIn(reply, fallback["markdown"][0].data["markdown"].content)

    async def test_trickcal_board_command_then_file_upload_in_same_group(self):
        from qbot_trickcal import trickcal_board
        from plugins import trickcal as trickcal_plugin

        document = {
            "version": 1, "units": [[10016, 3]], "cards": [], "pets": [],
            "boards": [[10016, {"selectedNodes": [100], "plannedNodes": [101]}]],
            "filter": [], "stepFilter": [], "statFilter": [],
            "purpleWeight": 8, "goldWeight": 1000,
        }
        raw = json.dumps(document, separators=(",", ":"))
        attachment = [{
            "url": "https://gchat.qpic.cn/export.json", "filename": "collection.json",
            "content_type": "application/json", "size": len(raw.encode()),
        }]
        with patch.object(trickcal_plugin, "get_store", return_value=self.store), \
             patch.object(self.store, "throttle"), \
             patch.object(trickcal_board, "fetch_qq_export", new_callable=AsyncMock,
                          return_value=raw) as fetch:
            await handle_event(
                self.bot, event_for("full", "/tr 蜡笔板 导入", role="member", group="group-a")
            )
            self.assertEqual(self.bot.send.call_count, 1)
            self.assertIn("等待上传蜡笔板文件", self.reply())

            # The same user's upload in another group must not consume this wait.
            self.bot.send.reset_mock()
            await handle_event(
                self.bot, event_for("full", "", role="member", attachments=attachment, group="group-b")
            )
            self.bot.send.assert_not_awaited()
            fetch.assert_not_awaited()

            self.bot.send.reset_mock()
            await handle_event(
                self.bot, event_for("full", "", role="member", attachments=attachment, group="group-a")
            )
            self.assertEqual(self.bot.send.call_count, 1)
            self.assertIn("蜡笔板已导入", self.reply())
            fetch.assert_awaited_once()

        who = toolbox.identity(self.bot, event_for("full", "", role="member", group="group-a"))
        board_store = trickcal_board.BoardStore(self.store)
        self.assertIsNotNone(board_store.get(who))
        self.assertFalse(board_store.pending_import(who))

    async def test_trickcal_board_next_non_json_message_cancels_wait(self):
        from qbot_trickcal import trickcal_board
        from plugins import trickcal as trickcal_plugin

        attachment = [{
            "url": "https://gchat.qpic.cn/export.json", "filename": "collection.json",
            "content_type": "application/json", "size": 10,
        }]
        with patch.object(trickcal_plugin, "get_store", return_value=self.store), \
             patch.object(self.store, "throttle"), \
             patch.object(trickcal_board, "fetch_qq_export", new_callable=AsyncMock) as fetch:
            await handle_event(
                self.bot, event_for("full", "/tr 蜡笔板 导入", role="member", group="group-a")
            )
            self.bot.send.reset_mock()
            await handle_event(
                self.bot, event_for("full", "这不是文件", role="member", group="group-a")
            )
            self.assertEqual(self.bot.send.call_count, 1)
            self.assertIn("导入已取消", self.reply())
            self.assertIn("下一条消息不是", self.reply())

            self.bot.send.reset_mock()
            await handle_event(
                self.bot, event_for("full", "", role="member", attachments=attachment, group="group-a")
            )
            self.bot.send.assert_not_awaited()
            fetch.assert_not_awaited()

        who = toolbox.identity(self.bot, event_for("full", "", role="member", group="group-a"))
        self.assertFalse(trickcal_board.BoardStore(self.store).pending_import(who))
