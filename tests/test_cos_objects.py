from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from bot_tools.media_objects import CosObjects, CosSettings, LocalObjects
from bot_tools.storage import ToolError


class MissingObject(Exception):
    def get_status_code(self):
        return 404

    def get_error_code(self):
        return "NoSuchKey"


class FakeStream:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def get_raw_stream(self):
        return self.stream


class FakeCosClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise MissingObject()
        return {"Content-Length": str(len(self.objects[Key]))}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = bytes(Body)
        return {"ETag": "synthetic"}

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise MissingObject()
        return {
            "Content-Length": str(len(self.objects[Key])),
            "Body": FakeStream(self.objects[Key]),
        }

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)
        return {}


class CosObjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeCosClient()
        self.settings = CosSettings(
            bucket="qbot-1250000000",
            region="ap-guangzhou",
            secret_id="test-id",
            secret_key="test-key",
        )
        self.backend = CosObjects(
            self.root, self.settings, client=self.client, errors=(MissingObject,)
        )

    def test_content_addressed_round_trip_and_deduplication(self):
        data = b"one-image"
        first = self.backend.put(data)
        second = self.backend.put(data)
        self.assertEqual(first, second)
        self.assertEqual(self.backend.read(first), data)
        self.assertEqual(len(self.client.objects), 1)

    def test_missing_cos_object_reads_local_fallback(self):
        local = LocalObjects(self.root)
        key = local.put(b"local-original")
        self.assertEqual(self.backend.read(key), b"local-original")

    def test_missing_without_fallback_fails_closed(self):
        settings = CosSettings(
            bucket="qbot-1250000000",
            region="ap-guangzhou",
            secret_id="test-id",
            secret_key="test-key",
            local_fallback=False,
        )
        backend = CosObjects(
            self.root, settings, client=self.client, errors=(MissingObject,)
        )
        with self.assertRaisesRegex(ToolError, "COS 中缺少"):
            backend.read("a" * 64)

    def test_thumbnail_is_stored_in_separate_cos_prefix(self):
        output = io.BytesIO()
        Image.new("RGB", (500, 500), "red").save(output, "PNG")
        original = output.getvalue()
        preview = self.backend.thumbnail(original)
        self.assertTrue(preview.startswith(b"\xff\xd8\xff"))
        self.assertTrue(any("/thumbnails/" in key for key in self.client.objects))

    def test_check_round_trip_removes_probe(self):
        self.backend.verify_round_trip()
        self.assertEqual(self.client.objects, {})


if __name__ == "__main__":
    unittest.main()
