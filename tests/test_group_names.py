from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot_tools.group_names import claim, refresh
from bot_tools.storage import Store


class GroupNameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    async def test_official_name_is_saved_and_openid_is_not_persisted(self):
        response = SimpleNamespace(
            status_code=200,
            content=json.dumps({"group_name": "测试官方群"}, ensure_ascii=False).encode(),
        )
        adapter = SimpleNamespace(
            get_api_base=lambda: "https://api.sgroup.qq.com",
            request=AsyncMock(return_value=response),
        )
        bot = SimpleNamespace(
            adapter=adapter,
            get_authorization_header=AsyncMock(return_value={"Authorization": "hidden"}),
        )
        await refresh(self.store, bot, "sensitive-group-openid", "a" * 64)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM group_metadata").fetchone()
            self.assertEqual(row["official_name"], "测试官方群")
            self.assertNotIn("sensitive-group-openid", str(dict(row)))
        self.assertIn("/v2/groups/sensitive-group-openid/info", str(adapter.request.call_args.args[0].url))

    async def test_failed_api_is_cosmetic_and_rate_limited(self):
        response = SimpleNamespace(status_code=403, content=b'{"code":11253}')
        adapter = SimpleNamespace(
            get_api_base=lambda: "https://api.sgroup.qq.com",
            request=AsyncMock(return_value=response),
        )
        bot = SimpleNamespace(
            adapter=adapter,
            get_authorization_header=AsyncMock(return_value={}),
        )
        await refresh(self.store, bot, "group", "b" * 64)
        await refresh(self.store, bot, "group", "b" * 64)
        adapter.request.assert_awaited_once()
        with self.store.connect() as db:
            error = db.execute("SELECT last_error FROM group_metadata").fetchone()[0]
        self.assertIn("11253", error)
        self.assertFalse(claim(self.store, "b" * 64))


if __name__ == "__main__":
    unittest.main()
