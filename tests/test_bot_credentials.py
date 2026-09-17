from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_tools.bot_credentials import BotCredentialStore, configure_runtime
from bot_tools.storage import Store, ToolError


class BotCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.credentials = BotCredentialStore(self.store)

    def test_secret_is_encrypted_and_never_returned_as_metadata(self):
        secret = "synthetic-app-secret-OnlyForTests"
        created = self.credentials.save("bot-one", secret, label="一号机器人")
        self.assertTrue(created["created"])
        metadata = self.credentials.metadata()
        self.assertEqual(metadata[0]["label"], "一号机器人")
        self.assertNotIn(secret, repr(metadata))
        with self.store.connect() as db:
            stored = bytes(db.execute(
                "SELECT secret FROM qq_bot_credentials WHERE app_id='bot-one'"
            ).fetchone()[0])
        self.assertNotIn(secret.encode(), stored)
        runtime = self.credentials.runtime()
        self.assertEqual(runtime[0].secret, secret)
        self.assertNotIn(secret, repr(runtime[0]))
        self.assertEqual(self.credentials.runtime_one("bot-one").secret, secret)

    def test_blank_secret_keeps_existing_ciphertext(self):
        self.credentials.save("bot-one", "first-secret", label="旧名称")
        with self.store.connect() as db:
            before = bytes(db.execute(
                "SELECT secret FROM qq_bot_credentials WHERE app_id='bot-one'"
            ).fetchone()[0])
        self.credentials.save("bot-one", "", label="新名称", enabled=False)
        with self.store.connect() as db:
            after = bytes(db.execute(
                "SELECT secret FROM qq_bot_credentials WHERE app_id='bot-one'"
            ).fetchone()[0])
        self.assertEqual(before, after)
        self.assertEqual(self.credentials.metadata()[0]["label"], "新名称")
        self.assertEqual(self.credentials.runtime(), [])
        self.assertIsNone(self.credentials.runtime_one("bot-one"))

    def test_missing_master_key_never_silently_replaces_it(self):
        self.credentials.save("bot-one", "first-secret")
        (Path(self.temp.name) / "secrets" / "qq-bots-master.key").unlink()
        with self.assertRaisesRegex(ToolError, "主密钥丢失"):
            self.credentials.runtime()

    def test_legacy_environment_imports_once_without_overwriting_admin_change(self):
        first = [{"id": "bot-one", "secret": "legacy-secret", "use_websocket": True}]
        with patch.dict(os.environ, {
            "BOT_DATA_DIR": self.temp.name, "QQ_BOTS": json.dumps(first),
        }, clear=True):
            loaded = configure_runtime(self.store)
            self.assertEqual(loaded[0].secret, "legacy-secret")
            self.assertNotIn("QQ_BOTS", os.environ)
            self.assertNotIn("QQ_APP_SECRET", os.environ)
        self.credentials.save("bot-one", "admin-updated-secret")
        with patch.dict(os.environ, {
            "BOT_DATA_DIR": self.temp.name, "QQ_BOTS": json.dumps(first),
        }, clear=True):
            loaded = configure_runtime(self.store)
            self.assertEqual(loaded[0].secret, "admin-updated-secret")
            self.assertEqual(json.loads(os.environ["QQBOT_ACTIVE_BOT_IDS"]), ["bot-one"])


if __name__ == "__main__":
    unittest.main()
