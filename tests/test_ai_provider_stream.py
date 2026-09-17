from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from bot_tools import ai_provider
from bot_tools.storage import ToolError


KEY = "fake-stream-test-key.12345678"


def frame(data, newline="\n"):
    return ("data: " + json.dumps(data, ensure_ascii=False) + newline * 2).encode()


def delta(content=None, **fields):
    return {"choices": [{"index": 0, "delta": {"content": content, **fields}}]}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, body, size=7, error=None):
        self.body = body
        self.size = size
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.body), self.size):
            yield self.body[offset:offset + self.size]
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class AIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, stream, **kwargs):
        self.calls = []

        def handler(request):
            self.calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/event-stream;charset=UTF-8"}, stream=stream)

        return await ai_provider.complete("glm", "cn", KEY, [], transport=httpx.MockTransport(handler), **kwargs)

    async def assert_failure(self, body, fragment=None, error=None):
        stream = Chunks(body, error=error)
        with self.assertRaises(ToolError) as caught:
            await self.invoke(stream)
        self.assertNotIn(KEY, str(caught.exception))
        if fragment:
            self.assertIn(fragment, str(caught.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(stream.closed)

    async def test_sse_utf8_bom_boundaries_usage_only_and_ignored_reasoning(self):
        body = (b"\xef\xbb\xbf: keepalive\r\n\r\n"
                + frame(delta(None, role="assistant", reasoning_content=KEY), "\r\n")
                + frame(delta("你好"), "\r\n")
                + frame(delta("，世界！"), "\r\n")
                + frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}, "\r\n")
                + frame({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}, "\r\n")
                + b"data: [DONE]\r\n\r\n")
        stream = Chunks(body, size=1)
        result = await self.invoke(stream)
        self.assertEqual(result, ai_provider.Completion("你好，世界！", 5, 3, True))
        self.assertTrue(stream.closed)
        payload = json.loads(self.calls[0].content)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["model"], "glm-4.7-flash")
        self.assertEqual(self.calls[0].extensions["timeout"]["read"], 90)
        self.assertEqual(self.calls[0].extensions["timeout"]["connect"], 10)

    async def test_multiline_event_and_cr_only_newlines(self):
        body = (b'event: message\rdata: {"choices":\r'
                b'data: [{"delta":{"content":"hello"}}]}\r\r'
                b'data: [DONE]\r\r')
        result = await self.invoke(Chunks(body, size=1))
        self.assertEqual(result, ai_provider.Completion("hello"))

    async def test_secret_split_across_events_is_redacted_before_display_limit(self):
        body = (frame(delta(KEY[:12])) + frame(delta(KEY[12:] + "\x00" + "你" * 500))
                + b"data: [DONE]\n\n")
        result = await self.invoke(Chunks(body))
        self.assertTrue(result.text.startswith("[密钥已隐藏]"))
        self.assertNotIn(KEY, result.text)
        self.assertNotIn("\x00", result.text)
        self.assertEqual(len(result.text), ai_provider.MAX_REPLY_CHARS)

    async def test_content_with_usage_and_token_limit_finish_is_accepted(self):
        data = {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 256}}
        self.assertEqual(await self.invoke(Chunks(frame(data) + b"data: [DONE]\n\n")),
                         ai_provider.Completion("hello", 12, 256, True))

    async def test_invalid_usage_does_not_crash_or_fabricate_tokens(self):
        for usage in ([], "invalid", {"prompt_tokens": True, "completion_tokens": 3},
                      {"prompt_tokens": 1, "completion_tokens": -1}, {"prompt_tokens": 1}):
            with self.subTest(usage=usage):
                body = frame({**delta("hello"), "usage": usage}) + b"data: [DONE]\n\n"
                self.assertEqual(await self.invoke(Chunks(body)), ai_provider.Completion("hello"))

    async def test_eof_without_done_discards_even_finished_partial_reply(self):
        prefix = frame(delta("partial"))
        for ending in (b"", b"data: [DONE]", b"data: [DONE]\n",
                       frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})):
            await self.assert_failure(prefix + ending, "传输中断")

    async def test_in_stream_errors_are_not_relayed(self):
        for ending in (frame({"error": {"message": KEY}}),
                       ("event: error\ndata: " + KEY + "\n\n").encode()):
            await self.assert_failure(frame(delta("partial")) + ending, "生成回复时返回错误")

    async def test_unsafe_finish_reasons_and_reasoning_only_are_not_relayed(self):
        for reason in ("sensitive", "content_filter", "network_error", "tool_calls"):
            body = frame({"choices": [{"delta": {"content": KEY}, "finish_reason": reason}]})
            await self.assert_failure(body + b"data: [DONE]\n\n", "未能完成")
        await self.assert_failure(frame(delta(None, reasoning_content=KEY, tool_calls=[{"name": KEY}]))
                                  + b"data: [DONE]\n\n", "格式异常")

    async def test_other_choices_are_not_mixed_into_reply(self):
        body = frame({"choices": [{"index": 1, "delta": {"content": KEY}},
                                   {"index": 0, "delta": {"content": "hello"}}]})
        self.assertEqual((await self.invoke(Chunks(body + b"data: [DONE]\n\n"))).text, "hello")

    async def test_response_cap_covers_comments_and_unterminated_lines(self):
        for body in (b":" + b"x" * 140000, b": keepalive\n\n" * 12000,
                     frame(delta("x" * 140000))):
            await self.assert_failure(body, "过大")

    async def test_malformed_json_utf8_and_schema_are_safe_errors(self):
        for body in (b"data: not-json\n\n", b"data: \xff\n\n", frame([]),
                     frame({"choices": None}), frame({"choices": [None]}),
                     frame({"choices": [{"delta": "invalid"}]}), frame(delta([KEY]))):
            await self.assert_failure(body, "格式异常")

    async def test_partial_read_timeout_or_disconnect_does_not_return_partial(self):
        for error, expected in ((httpx.ReadTimeout(KEY), "连续 90 秒"),
                                (httpx.RemoteProtocolError(KEY), "传输中断")):
            await self.assert_failure(frame(delta("partial")), expected, error)

    async def test_http_exception_types_are_distinct_safe_and_not_retried(self):
        for error, expected in ((httpx.ConnectTimeout(KEY), "连接超时（10 秒）"),
                                (httpx.ReadTimeout(KEY), "连续 90 秒"),
                                (httpx.WriteTimeout(KEY), "发送或连接排队超时"),
                                (httpx.PoolTimeout(KEY), "发送或连接排队超时"),
                                (httpx.ConnectError(KEY), "DNS"),
                                (httpx.RemoteProtocolError(KEY), "传输中断"),
                                (httpx.ReadError(KEY), "网络传输异常")):
            calls = []

            def handler(request):
                calls.append(request)
                raise error

            with self.assertRaises(ToolError) as caught:
                await ai_provider.complete("glm", "cn", KEY, [], transport=httpx.MockTransport(handler))
            self.assertIn(expected, str(caught.exception))
            self.assertIn("没有自动重试", str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(len(calls), 1)

    async def test_glm_limit_codes_distinguish_busy_account_limit_and_balance(self):
        for code, expected in (("1113", "余额不足"), ("1302", "请求过于频繁"),
                               ("1305", "服务繁忙"), (1304, "今日调用次数"),
                               ("1308", "使用额度"), ("1310", "每周或每月")):
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(429, json={"error": {"code": code, "message": KEY}})

            with self.assertRaises(ToolError) as caught:
                await ai_provider.complete("glm", "cn", KEY, [], transport=httpx.MockTransport(handler))
            self.assertIn(expected, str(caught.exception))
            self.assertIn(str(code), str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(len(calls), 1)

    async def test_bad_glm_error_bodies_fall_back_to_safe_status_error(self):
        for body in (KEY.encode(), b"x" * 5000, b'[]', b'{"error":[]}',
                     json.dumps({"error": {"code": KEY}}).encode(),
                     b'{"error":{"code":[]}}', b'{"error":{"code":9999}}'):
            stream = Chunks(body)
            transport = httpx.MockTransport(lambda _: httpx.Response(429, stream=stream))
            with self.assertRaisesRegex(ToolError, "余额或调用额度不足") as caught:
                await ai_provider.complete("glm", "cn", KEY, [], transport=transport)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertTrue(stream.closed)

    async def test_total_deadline_stops_continuous_heartbeats(self):
        class Heartbeats(Chunks):
            async def __aiter__(self):
                while True:
                    yield b": keepalive\n\n"
                    await asyncio.sleep(0.005)

        stream = Heartbeats(b"")
        with patch.object(ai_provider, "GLM_TOTAL_TIMEOUT", 0.03):
            with self.assertRaisesRegex(ToolError, "总时限"):
                await self.invoke(stream)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(stream.closed)

    async def test_cancellation_propagates_and_closes_stream(self):
        ready = asyncio.Event()

        class Waiting(Chunks):
            async def __aiter__(self):
                yield frame(delta("partial"))
                ready.set()
                await asyncio.Event().wait()

        stream = Waiting(b"")
        task = asyncio.create_task(self.invoke(stream))
        await asyncio.wait_for(ready.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)
