"""Core extensions remain inert unless an optional plugin registers them."""
import tempfile
import unittest
from unittest.mock import patch

from bot_tools import community, group_extensions
from bot_tools.storage import Identity, Store, ToolError
from bot_tools.web_admin import WebAdmin


class GroupExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.empty = patch.dict(group_extensions._extensions, {}, clear=True)
        self.empty.start()
        self.addCleanup(self.empty.stop)

    def test_no_plugin_has_no_fields_and_preserves_legacy_records(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            who = Identity("test", "group:one", "owner", False, "owner")
            store.register(who)
            with store.state(who.scope_key) as doc:
                doc.update(server="历史小区", hunt_rules={"历史怪": [4, 6]})
            admin = WebAdmin(store)
            row = admin.groups(1)["items"][0]
            self.assertNotIn("server", row)
            self.assertNotIn("hunt_rules", row)
            self.assertEqual(row["plugin_fields"], [])
            self.assertEqual(row["plugin_details"], [])
            self.assertNotIn("狩猎", community.management(store, who, "group", ""))
            with self.assertRaises(ToolError):
                community.management(store, who, "group", "server 新小区")
            admin.update_group(who.scope_key, {"quota": 222, "server": "不应覆盖"})
            with store.state(who.scope_key) as doc:
                self.assertEqual(doc["server"], "历史小区")
                self.assertEqual(doc["hunt_rules"], {"历史怪": [4, 6]})
                self.assertEqual(doc["quota"], 222)

    def test_optional_callbacks_default_empty_and_registration_replaces(self):
        extension = group_extensions.GroupExtension(
            help_lines=("示例设置",), overview=lambda doc: ["示例总览"],
            configure=lambda doc, args: "设置成功" if args == ["example"] else None,
            summary=lambda doc: {"example": "enabled"},
        )
        group_extensions.register("example", extension)
        group_extensions.register("example", extension)
        self.assertEqual(group_extensions.help_lines(), ["示例设置"])
        self.assertEqual(group_extensions.overview({}), ["示例总览"])
        self.assertEqual(group_extensions.configure({}, ["example"]), "设置成功")
        self.assertIsNone(group_extensions.configure({}, ["other"]))
        self.assertEqual(group_extensions.summary({}), {"example": "enabled"})
        self.assertEqual(group_extensions.search_keys(), [])
        self.assertEqual(group_extensions.admin_fields({}), [])
        self.assertEqual(group_extensions.admin_details({}), [])
        doc = {"kept": True}
        group_extensions.admin_update(doc, {"unused": True})
        self.assertEqual(doc, {"kept": True})
