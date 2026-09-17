from __future__ import annotations

import unittest

from bot_tools.storage import ToolError
from message_ui import public_error_message


class PublicErrorTests(unittest.TestCase):
    def test_internal_deployment_details_are_replaced(self):
        fallback = "服务暂时不可用，请稍后重试或联系管理员。"
        private_messages = (
            "COS_BUCKET 必须是包含 APPID 的完整桶名。",
            "AI 加密主密钥丢失，请管理员从备份恢复。",
            "B站动态浏览器降级组件启动失败。",
            "请运行 bot.cmd fflogs-setup。",
        )
        for detail in private_messages:
            with self.subTest(detail=detail):
                self.assertEqual(
                    public_error_message(ToolError(detail), fallback),
                    fallback,
                )

    def test_actionable_user_error_is_preserved(self):
        detail = "一次最多上传 10 张图片，请分批发送。"
        self.assertEqual(
            public_error_message(ToolError(detail), "服务暂时不可用。"),
            detail,
        )

    def test_board_login_copy_does_not_describe_admin_architecture(self):
        from qbot_trickcal.trickcal_web import WEB_ROOT
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("机器人管理后台账号", page)
        self.assertIn("管理你自己的角色与蜡笔板节点", page)


if __name__ == "__main__":
    unittest.main()
