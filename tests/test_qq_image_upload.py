from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nonebot.drivers import URL
from PIL import Image

from bot_tools import qq_image_upload


class QQImageUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        qq_image_upload._url_cache.clear()

    async def test_group_multipart_upload_returns_and_caches_safe_raw_url(self):
        output = io.BytesIO()
        Image.new("RGB", (720, 900), "white").save(output, "JPEG")
        image = output.getvalue()
        raw_url = (
            "https://qqbot-file-upload-1251316161.cos.accelerate.myqcloud.com/"
            "test/card.jpg?sign=temporary"
        )
        prepare = SimpleNamespace(
            upload_id="upload-1",
            block_size=str(len(image)),
            upload_config=SimpleNamespace(concurrency=2),
            parts=[SimpleNamespace(index=1, presigned_url="https://upload.example/part")],
        )
        bot = SimpleNamespace(
            self_id="bot-1",
            adapter=SimpleNamespace(
                get_api_base=lambda: URL("https://api.sgroup.qq.com/")
            ),
            post_group_upload_prepare=AsyncMock(return_value=prepare),
            post_group_upload_part_finish=AsyncMock(return_value=None),
            post_c2c_upload_prepare=AsyncMock(),
            post_c2c_upload_part_finish=AsyncMock(),
            put_upload_part=AsyncMock(return_value=None),
            _request=AsyncMock(return_value={"raw_url": raw_url, "ttl": 86400}),
        )
        target = qq_image_upload.UploadTarget("group", "group-a")

        expected_url = raw_url + "&response-content-type=image%2Fjpeg"
        self.assertEqual(
            await qq_image_upload.public_image_url(bot, target, image), expected_url
        )
        self.assertEqual(
            await qq_image_upload.public_image_url(bot, target, image), expected_url
        )
        bot.post_group_upload_prepare.assert_awaited_once()
        bot.put_upload_part.assert_awaited_once()
        bot.post_group_upload_part_finish.assert_awaited_once()
        bot._request.assert_awaited_once()
        request = bot._request.await_args.args[0]
        self.assertIn("/v2/groups/group-a/files", str(request.url))
        self.assertEqual(request.json, {"upload_id": "upload-1"})

    async def test_rejects_non_qq_raw_url(self):
        output = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(output, "JPEG")
        image = output.getvalue()
        prepare = SimpleNamespace(
            upload_id="upload-2",
            block_size=len(image),
            upload_config=SimpleNamespace(concurrency=1),
            parts=[SimpleNamespace(index=1, presigned_url="https://upload.example/part")],
        )
        bot = SimpleNamespace(
            self_id="bot-1",
            adapter=SimpleNamespace(
                get_api_base=lambda: URL("https://api.sgroup.qq.com/")
            ),
            post_group_upload_prepare=AsyncMock(return_value=prepare),
            post_group_upload_part_finish=AsyncMock(return_value=None),
            put_upload_part=AsyncMock(return_value=None),
            _request=AsyncMock(return_value={
                "raw_url": "https://attacker.example/card.jpg", "ttl": 86400,
            }),
        )
        with self.assertRaisesRegex(RuntimeError, "usable raw URL"):
            await qq_image_upload.public_image_url(
                bot, qq_image_upload.UploadTarget("group", "group-a"), image,
            )

    def test_markdown_url_does_not_duplicate_existing_content_type(self):
        raw_url = (
            "https://qqbot-file-upload-1251316161.cos.accelerate.myqcloud.com/"
            "test/card.jpg?sign=temporary&response-content-type=text%2Fplain"
        )
        image_url = qq_image_upload._markdown_image_url(raw_url)

        self.assertEqual(image_url.count("response-content-type="), 1)
        self.assertIn("response-content-type=text%2Fplain", image_url)
