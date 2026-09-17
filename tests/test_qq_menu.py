import copy
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import qq_menu


class MenuTests(unittest.TestCase):
    def test_credentials_can_be_loaded_from_encrypted_admin_store(self):
        from bot_tools.bot_credentials import BotCredentialStore
        from bot_tools.storage import Store

        with tempfile.TemporaryDirectory() as temp:
            BotCredentialStore(Store(temp)).save("stored-bot", "stored-secret")
            with patch.dict(os.environ, {"BOT_DATA_DIR": temp}, clear=True):
                credentials = qq_menu.load_credentials(None)
        self.assertEqual(credentials.app_id, "stored-bot")
        self.assertEqual(credentials.client_secret, "stored-secret")

    def test_bundled_panels_keep_ff14_as_one_plugin_entry(self):
        config = qq_menu.load_config()
        ff14_children = {"market", "gather", "sales", "recip", "craftcost", "cheapest", "dps", "raid", "fsx", "ofish", "hunt", "quest", "search", "house", "weather", "fflogs", "luck", "gate"}
        for definition in config["panels"]:
            items = definition["panel"]["items"]
            names = [item["name"] for item in items]
            self.assertEqual(len(names), 20)
            self.assertEqual(len(set(names)), 20)
            self.assertEqual(names.count("ff14"), 1)
            self.assertEqual(names.count("tr"), 1)
            if definition["scope"] in {"c2c", "group"}:
                self.assertEqual(names.count("ai"), 1)
            if definition["scope"] == "group":
                self.assertEqual(names.count("bili"), 1)
            self.assertFalse(ff14_children & set(names))
            self.assertTrue({"cat", "gif", "image", "tex", "vote", "lottery", "bot", "toolbox"} <= set(names))
            for item in items:
                if item["name"] in {"bot", "group", "command"}:
                    self.assertEqual(item["only_admin"], definition["scope"] != "c2c")
        menu_commands = [item.get("send_message") for item in config["custom_menu"]["items"]]
        self.assertEqual(menu_commands.count("/ff14"), 1)
        self.assertEqual(menu_commands.count("/tr"), 1)
        self.assertNotIn("/fflogs", menu_commands)

    def sample(self):
        definition = {"scope":"group", "target_type":"all", "panel":{
            "remark":"nonebot-qq-menu:group", "items":[{"type":"command", "name":"ff14", "desc":"FF14插件", "only_admin":False}]}}
        config = {"custom_menu":None, "panels":[definition]}
        after = {"group":[{"panel_id":"managed", **copy.deepcopy(definition)}]}
        before = copy.deepcopy(after)
        before["group"][0]["panel"]["items"][0]["name"] = "market"
        return config, before, after

    def test_sync_updates_existing_panel_then_reads_back(self):
        config, before, after = self.sample()
        client = MagicMock()
        client.request.return_value = {"version":2}
        with patch.object(qq_menu, "collect_state", side_effect=[({}, before), ({}, after)]) as collect, redirect_stdout(io.StringIO()) as output:
            qq_menu.sync(client, config, force_menu=False)
        client.request.assert_called_once_with("PUT", "/v2/panels/managed", {"panel":config["panels"][0]["panel"]})
        self.assertEqual(collect.call_count, 2)
        self.assertIn("平台回读验证通过", output.getvalue())

    def test_write_success_but_stale_readback_is_not_reported_as_verified(self):
        config, before, after = self.sample()
        with patch.object(qq_menu, "collect_state", return_value=({}, before)), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(qq_menu.MenuManagerError, "回读"):
                qq_menu.sync(MagicMock(), config, force_menu=False)
        self.assertNotIn("验证通过", output.getvalue())

    def test_already_synced_does_not_write(self):
        config, before, after = self.sample()
        client = MagicMock()
        with patch.object(qq_menu, "collect_state", return_value=({}, after)), redirect_stdout(io.StringIO()):
            qq_menu.sync(client, config, force_menu=False)
        client.request.assert_not_called()

    def test_unrelated_panels_are_not_overwritten(self):
        config, before, after = self.sample()
        before["group"].append({"panel_id":"unrelated", "panel":{"remark":"someone-else", "items":[]}})
        client = MagicMock()
        with patch.object(qq_menu, "collect_state", side_effect=[({}, before), ({}, after)]), redirect_stdout(io.StringIO()):
            qq_menu.sync(client, config, force_menu=False)
        self.assertEqual(client.request.call_args.args[1], "/v2/panels/managed")

    def test_mismatched_menu_still_requires_explicit_force(self):
        config, before, after = self.sample()
        config["custom_menu"] = {"items":[{"type":"send_message", "name":"目录", "send_message":"/toolbox"}]}
        current = {"menu":{"items":[{"type":"send_message", "name":"旧菜单", "send_message":"/otter"}]}}
        client = MagicMock()
        with patch.object(qq_menu, "collect_state", return_value=(current, before)), self.assertRaises(qq_menu.MenuManagerError):
            qq_menu.sync(client, config, force_menu=False)
        client.request.assert_not_called()
