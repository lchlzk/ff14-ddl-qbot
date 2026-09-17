from __future__ import annotations

import unittest

from nonebot.adapters.qq import Bot, Message, MessageSegment

from bot_tools.qq_buttons import command_keyboard, pagination_keyboard
from qbot_trickcal.trickcal_board import PaginatedReply


class QQButtonTests(unittest.TestCase):
    def test_pagination_commands_are_sender_only_and_auto_send(self):
        keyboard = pagination_keyboard("/tr before", "/tr next", "user-openid")
        self.assertIsNotNone(keyboard)
        buttons = keyboard.content.rows[0].buttons
        self.assertEqual([button.render_data.label for button in buttons], ["上一页", "下一页"])
        self.assertEqual([button.action.data for button in buttons], ["/tr before", "/tr next"])
        self.assertTrue(all(button.action.type == 2 and button.action.enter for button in buttons))
        self.assertTrue(all(button.action.permission.type == 0 for button in buttons))
        self.assertTrue(all(
            button.action.permission.specify_user_ids == ["user-openid"] for button in buttons
        ))
        reply = PaginatedReply(
            "第 1 页", base_command="/tr 蜡笔板 未点 攻击", page=1, total=9,
        )
        self.assertEqual(str(reply), "第 1 页")
        rich_message = (
            MessageSegment.markdown(str(reply)) + MessageSegment.keyboard(keyboard)
        )
        rich_payload = Bot._extract_send_message(rich_message, escape_text=False)
        self.assertIsNone(rich_payload["content"])
        self.assertEqual(rich_payload["markdown"].content, "第 1 页")
        self.assertIs(rich_payload["keyboard"], keyboard)
        # QQ group messages reject plain content + keyboard as invalid
        # markdown. If native markdown is unavailable, production sends this
        # keyboard-only payload separately after the ordinary text result.
        payload = Bot._extract_send_message(
            Message(MessageSegment.keyboard(keyboard)), escape_text=False,
        )
        self.assertIsNone(payload["content"])
        self.assertIs(payload["keyboard"], keyboard)

    def test_single_page_has_no_keyboard(self):
        self.assertIsNone(pagination_keyboard(None, None, "user-openid"))

    def test_action_keyboard_uses_three_columns_and_sender_permission(self):
        actions = [(f"节点 {index}", f"/tr mark {index}", 1) for index in range(5)]
        keyboard = command_keyboard(actions, "owner-openid")
        self.assertEqual(
            [len(row.buttons) for row in keyboard.content.rows], [3, 2],
        )
        buttons = [button for row in keyboard.content.rows for button in row.buttons]
        self.assertEqual([button.action.data for button in buttons], [
            "/tr mark 0", "/tr mark 1", "/tr mark 2", "/tr mark 3", "/tr mark 4",
        ])
        self.assertTrue(all(
            button.action.permission.specify_user_ids == ["owner-openid"]
            for button in buttons
        ))
