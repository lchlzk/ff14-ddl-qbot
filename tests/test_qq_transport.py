import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import nonebot
from nonebot.adapters.qq import Adapter, Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.drivers import Request, Response

from bot_tools.qq_transport import ReliableQQAdapter


class QQTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The transport override itself uses no initialized adapter state;
        # base request is mocked, so no live driver/account/network is involved.
        self.adapter = object.__new__(ReliableQQAdapter)
        self.request = Request("POST", "https://api.bot.qq.com/v2/groups/test/messages",
                               json={"msg_id":"test", "msg_seq":1, "content":"目录"})
        self.response = Response(200, content=b'{"id":"test-result"}')

    async def test_success_is_not_repeated(self):
        with patch.object(Adapter, "request", new_callable=AsyncMock, return_value=self.response) as request:
            self.assertIs(await self.adapter.request(self.request), self.response)
        request.assert_awaited_once_with(self.request)

    async def test_connect_failures_retry_same_prepared_request(self):
        for error in (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            with self.subTest(error=error):
                failures = [error("test-only"), error("test-only"), self.response]
                with patch.object(Adapter, "request", new_callable=AsyncMock, side_effect=failures) as request, patch("bot_tools.qq_transport.asyncio.sleep", new_callable=AsyncMock) as sleep:
                    self.assertIs(await self.adapter.request(self.request), self.response)
                    self.assertEqual(request.await_count, 3)
                    self.assertEqual(sleep.await_count, 2)
                    for call in request.await_args_list:
                        self.assertIs(call.args[0], self.request)
                        self.assertEqual(call.args[0].json["msg_seq"], 1)
                    self.assertEqual(self.request.json["content"], "目录")

    async def test_permanent_connection_failure_is_bounded(self):
        with patch.object(Adapter, "request", new_callable=AsyncMock, side_effect=httpx.ConnectError("test-only")) as request, patch("bot_tools.qq_transport.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(httpx.ConnectError):
                await self.adapter.request(self.request)
            self.assertEqual(request.await_count, 3)
            self.assertEqual(sleep.await_count, 2)

    async def test_ambiguous_delivery_and_cancellation_are_never_retried(self):
        for error in (httpx.ReadTimeout, httpx.ReadError, httpx.WriteTimeout, httpx.WriteError, httpx.RemoteProtocolError, RuntimeError, asyncio.CancelledError):
            with self.subTest(error=error), patch.object(Adapter, "request", new_callable=AsyncMock, side_effect=error("test-only")) as request, patch("bot_tools.qq_transport.asyncio.sleep", new_callable=AsyncMock) as sleep:
                with self.assertRaises(error):
                    await self.adapter.request(self.request)
                request.assert_awaited_once()
                sleep.assert_not_awaited()

    async def test_http_rejections_are_returned_without_retry(self):
        for status in (400, 401, 403, 429, 500):
            response = Response(status, content=b'{"code":123}')
            with self.subTest(status=status), patch.object(Adapter, "request", new_callable=AsyncMock, return_value=response) as request:
                self.assertIs(await self.adapter.request(self.request), response)
                request.assert_awaited_once()

    def test_adapter_name_still_qq(self):
        self.assertEqual(ReliableQQAdapter.get_name(), "QQ")

    async def test_runtime_account_add_replace_and_remove_do_not_restart_adapter(self):
        try:
            driver = nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi+~httpx+~websockets")
            driver = nonebot.get_driver()
        adapter = ReliableQQAdapter(driver)
        first = BotInfo(id="live-bot", token="", secret="first-secret")
        second = BotInfo(id="live-bot", token="", secret="second-secret")
        started = []

        async def hold(bot_info):
            started.append(bot_info.secret)
            await asyncio.Event().wait()

        with patch.object(adapter, "run_bot_websocket", new=hold):
            await adapter.replace_runtime_bot("live-bot", first)
            await asyncio.sleep(0)
            self.assertEqual(started, ["first-secret"])
            self.assertIn("live-bot", adapter.runtime_snapshot()["loaded"])
            self.assertIn("live-bot", adapter.runtime_snapshot()["connecting"])

            await adapter.replace_runtime_bot("live-bot", second)
            await asyncio.sleep(0)
            self.assertEqual(started, ["first-secret", "second-secret"])
            self.assertEqual(adapter.qq_config.qq_bots[0].secret, "second-secret")

            await adapter.replace_runtime_bot("live-bot", None)
            await asyncio.sleep(0)
            self.assertNotIn("live-bot", adapter.runtime_snapshot()["loaded"])
            self.assertNotIn("live-bot", adapter.runtime_snapshot()["connecting"])

    async def test_actual_qq_message_keeps_reply_sequence_across_connect_retry(self):
        try:
            driver = nonebot.get_driver()
        except ValueError:
            nonebot.init(driver="~fastapi+~httpx+~websockets")
            driver = nonebot.get_driver()
        self.adapter = ReliableQQAdapter(driver)
        bot = Bot(self.adapter, "test", BotInfo(id="test", token="", secret="test-only"))
        bot.get_authorization_header = AsyncMock(return_value={})
        event = GroupMessageCreateEvent.model_validate({
            "id":"message-test", "content":"/otter", "timestamp":"2026-09-04T00:00:00Z",
            "group_id":"test-group", "group_openid":"test-group",
            "author":{"id":"test-user", "member_openid":"test-user", "member_role":"member", "bot":False},
        })
        response = Response(200, content=b'{"id":"sent-test","timestamp":"2026-09-04T00:00:00Z"}')
        with patch.object(Adapter, "request", new_callable=AsyncMock, side_effect=[httpx.ConnectError("test-only"), response]) as request, patch("bot_tools.qq_transport.asyncio.sleep", new_callable=AsyncMock):
            await bot.send(event, "目录发送测试")
        self.assertEqual(request.await_count, 2)
        first, second = (call.args[0] for call in request.await_args_list)
        self.assertIs(first, second)
        self.assertEqual(first.json["msg_id"], "message-test")
        self.assertEqual(first.json["msg_seq"], 1)
        self.assertEqual(event._reply_seq, 1)
