from __future__ import annotations

import io
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nonebot
from PIL import Image

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi+~httpx+~websockets")

from bot_tools import community as c, games, media
from bot_tools.storage import Identity, Store, ToolError
from plugins import toolbox
from qbot_ff14.hunt import hunt
from qbot_ff14 import commands as ff14_commands
from qbot_ff14.integration import register

register()
from nonebot.adapters.qq.event import GroupMessageCreateEvent, GroupAtMessageCreateEvent, C2CMessageCreateEvent


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.owner = Identity("app","group:a","owner")
        self.member = Identity("app","group:a","member")
        self.other = Identity("app","group:b","member")
        self.private = Identity("app","private:owner","owner",True)
        for who in (self.owner,self.member,self.other,self.private):
            self.store.register(who)
        # A pre-upgrade group grant remains supported, but new superadmin
        # grants must use the private two-step workflow.
        with self.store.connect() as db:
            db.execute("UPDATE identities SET role='owner' WHERE actor=?", (self.owner.actor,))
        record = self.store.redeem_admin_token(self.private, self.store.create_admin_token())
        self.store.grant(record["code"], "owner")

    def test_identity_code_does_not_grant_role(self):
        self.assertFalse(self.store.is_admin(self.member))
        self.assertNotEqual(self.store.register(self.member)["code"],self.store.register(self.other)["code"])
        with self.assertRaises(ToolError):
            c.management(self.store,self.member,"group","quota 10")

    def test_group_admin_grant_only_this_scope(self):
        code = self.store.register(self.member)["code"]
        c.management(self.store,self.owner,"group","admin add "+code)
        self.assertTrue(self.store.is_admin(self.member))
        self.assertFalse(self.store.is_admin(self.member,owner=True))
        self.assertFalse(self.store.is_admin(self.other))
        with self.assertRaises(ToolError):
            c.management(self.store,self.member,"group","admin add "+code)
        with self.assertRaises(ToolError):
            c.management(self.store,self.owner,"group","admin add "+self.store.register(self.other)["code"])

    def test_qq_admin_and_owner_can_manage_current_group(self):
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                who = replace(self.member, group_role=role)
                self.assertTrue(self.store.is_admin(who))
                self.assertFalse(self.store.is_admin(who, owner=True))
                c.management(self.store, who, "group", "server 梦羽宝境")
                c.management(self.store, who, "group", "quota 200")
                c.custom_reply(self.store, who, "set 自动权限 | 已识别")
                c.management(self.store, who, "command", "disable cat")
                c.management(self.store, who, "command", "enable cat")
                hunt(self.store, who, "rule 测试怪 4 6")
                hunt(self.store, who, "kill 测试怪")
                hunt(self.store, who, "undo 测试怪")
                self.assertIn("梦羽宝境", c.management(self.store, who, "group", ""))

    def test_qq_role_is_not_cached_or_promoted_in_database(self):
        native = replace(self.member, group_role="owner")
        before = self.store.register(self.member)
        self.assertTrue(self.store.is_admin(native))
        registered = self.store.register(native)
        self.assertEqual(registered["role"], "member")
        self.assertEqual(registered["code"], before["code"])
        self.assertEqual(native.actor, self.member.actor)
        self.assertEqual(native.scope_key, self.member.scope_key)
        c.custom_reply(self.store, native, "set 昵称 | 不能作为权限证明")
        reopened = Store(self.temp.name)
        demoted = replace(native, group_role="member")
        self.assertFalse(reopened.is_admin(demoted))
        with self.assertRaises(ToolError):
            c.custom_reply(reopened, demoted, "del 昵称")

    def test_native_qq_role_does_not_follow_user_to_other_contexts(self):
        native = replace(self.member, group_role="admin")
        self.assertTrue(self.store.is_admin(native))
        for who in (
            self.other,
            Identity("another-app", "group:a", "member"),
            Identity("app", "private:member", "member", True, "owner"),
            Identity("app", "channel:a", "member", False, "owner"),
            replace(self.member, group_role="superadmin"),
            replace(self.member, group_role=None),
        ):
            with self.subTest(who=who):
                self.assertFalse(self.store.is_admin(who))

    def test_qq_group_owner_cannot_read_feedback_or_delegate(self):
        n = self.store.feedback_add(self.member, "私密反馈")
        for role in ("owner", "admin"):
            who = replace(self.member, group_role=role)
            for raw in ("list", f"show {n}", f"done {n}", "closed"):
                with self.subTest(role=role, command=raw), self.assertRaises(ToolError):
                    c.management(self.store, who, "comment", raw)
            for op in ("add", "remove"):
                with self.assertRaises(ToolError):
                    c.management(self.store, who, "group", f"admin {op} "+self.store.register(self.owner)["code"])
            with self.assertRaises(ToolError):
                c.management(self.store, who, "bot", "admin add "+self.store.register(who)["code"])
        # Explicit server authorization still allows the private superadmin.
        self.assertIn("私密反馈", c.management(self.store, self.private, "comment", f"show {n}"))

    def test_group_grant_api_cannot_create_a_superadmin(self):
        code = self.store.register(self.member)["code"]
        with self.assertRaises(ToolError):
            self.store.grant(code, "owner", self.member.scope_key)
        self.assertFalse(self.store.is_admin(self.member, owner=True))

    def test_qq_admin_can_close_other_members_activities(self):
        who = replace(self.member, group_role="admin")
        c.activities(self.store, self.owner, "vote", "create 投票 | A | B")
        self.assertIn("已结束", c.activities(self.store, who, "vote", "close 1"))
        c.activities(self.store, self.owner, "lottery", "create 抽奖 | 1")
        c.activities(self.store, self.owner, "lottery", "join 1 小张")
        self.assertIn("中奖名单", c.activities(self.store, who, "lottery", "draw 1"))

    def test_native_role_does_not_erase_explicit_grants(self):
        code = self.store.register(self.member)["code"]
        self.store.grant(code, "admin")
        self.assertTrue(self.store.is_admin(replace(self.member, group_role="member")))
        self.assertFalse(self.store.is_admin(replace(self.member, group_role="owner"), owner=True))
        self.store.grant(code, "member")
        self.assertFalse(self.store.is_admin(self.member))
        self.assertTrue(self.store.is_admin(replace(self.member, group_role="admin")))

    def test_whoami_distinguishes_group_owner_and_superadmin(self):
        native = replace(self.member, group_role="owner")
        text = c.management(self.store, native, "bot", "whoami")
        self.assertIn("当前权限：QQ群主（仅本群）", text)
        self.assertIn("/custom_reply set", text)
        self.assertNotIn("身份码", text)
        self.assertNotIn("admin add", text)
        self.assertIn("当前权限：机器人总管理员（服务器授权）", c.management(self.store, self.owner, "bot", "whoami"))
        self.assertLessEqual(len(text.encode()), 1800)

    def test_settings_persist(self):
        c.management(self.store,self.owner,"group","server 梦羽宝境")
        c.management(self.store,self.owner,"group","quota 8")
        reopened = Store(self.temp.name)
        status = c.management(reopened,self.owner,"group","")
        self.assertIn("梦羽宝境",status)
        self.assertIn("8",status)

    def test_command_alias_and_recovery(self):
        c.management(self.store,self.owner,"command","disable recipe")
        with self.assertRaises(ToolError):
            c.gate(self.store,self.member,"recip")
        with self.assertRaises(ToolError):
            c.gate(self.store,self.member,"recipe")
        c.gate(self.store,self.other,"recip")
        with self.assertRaises(ToolError):
            c.management(self.store,self.owner,"command","disable bot")
        c.management(self.store,self.owner,"command","enable recipe")
        c.gate(self.store,self.member,"recip")

    def test_ff14_umbrella_switch_is_scoped_and_blocks_all_subcommands(self):
        c.management(self.store,self.owner,"command","disable ff14")
        for command in c.COMMAND_GROUPS["ff14"]:
            with self.subTest(command=command), self.assertRaises(ToolError) as error:
                c.gate(self.store,self.member,command)
            self.assertIn("FF14 插件",str(error.exception))
        for alias in ("recipe","reid","dpscheck","mitem","天气"):
            with self.subTest(alias=alias), self.assertRaises(ToolError):
                c.gate(self.store,self.member,alias)
        # Generic and Trickcal commands are not part of the FF14 umbrella.
        c.gate(self.store,self.member,"dice")
        c.gate(self.store,self.member,"tr")
        # The setting belongs only to group A.
        c.gate(self.store,self.other,"market")
        c.management(self.store,self.owner,"command","enable ff14")
        for command in c.COMMAND_GROUPS["ff14"]:
            c.gate(self.store,self.member,command)

    def test_trickcal_switch_can_be_disabled_in_another_group(self):
        other_admin = replace(self.other,group_role="owner")
        c.management(self.store,other_admin,"command","disable tr")
        with self.assertRaises(ToolError):
            c.gate(self.store,self.other,"tr")
        c.gate(self.store,self.member,"tr")
        c.gate(self.store,self.other,"market")

    def test_quota_resets_at_cst_midnight(self):
        doc = {"quota":1}
        before = datetime(2026,9,3,15,59,59,tzinfo=timezone.utc).timestamp()
        self.assertEqual(c.quota(doc,"a",True,before),(1,1))
        with self.assertRaises(ToolError):
            c.quota(doc,"a",True,before)
        self.assertEqual(c.quota(doc,"a",False,before+1),(0,1))

    def test_throttle_is_persistent(self):
        self.store.throttle("x",3,100)
        with self.assertRaises(ToolError):
            Store(self.temp.name).throttle("x",3,101)
        self.store.throttle("x",3,103)

    def test_feedback_private_owner_only(self):
        n = self.store.feedback_add(self.member,"帮我看看 /market 报错",100)
        for who in (self.owner,self.member,self.other):
            with self.assertRaises(ToolError):
                self.store.feedback_list(who,1)
        self.assertEqual(self.store.feedback_list(self.private,1)[0]["id"],n)
        self.assertIn("/market",c.management(self.store,self.private,"comment",f"show {n}"))
        self.store.feedback_close(self.private,n)
        self.assertEqual(self.store.feedback_list(self.private,1),[])
        self.assertEqual(len(self.store.feedback_list(self.private,1,True)),1)

    def test_feedback_length_rate_and_sql(self):
        for _ in range(5):
            self.store.feedback_add(self.member,"'; DROP TABLE feedback; --",100)
        with self.assertRaises(ToolError):
            self.store.feedback_add(self.member,"more",101)
        with self.assertRaises(ToolError):
            self.store.feedback_add(self.member,"a"*501,90000)
        self.store.feedback_add(self.member,"new day",90000)
        self.assertEqual(len(self.store.feedback_list(self.private,1)),4)

    def test_full_feedback_fits_message(self):
        n=self.store.feedback_add(self.member,"中"*500)
        text=c.management(self.store,self.private,"comment",f"show {n}")
        self.assertLessEqual(len(text.encode()),1800)
        self.assertEqual(text,toolbox.bounded(text))

    def test_custom_reply_exact_scoped_and_admin_only(self):
        with self.assertRaises(ToolError):
            c.custom_reply(self.store,self.member,"set 你好 | 你好呀")
        c.custom_reply(self.store,self.owner,"set 你好 | 你好呀")
        self.assertEqual(c.keyword_lookup(self.store,self.member," 你好 "),"你好呀")
        self.assertIsNone(c.keyword_lookup(self.store,self.member,"大家你好"))
        self.assertIsNone(c.keyword_lookup(self.store,self.other,"你好"))
        with self.assertRaises(ToolError):
            c.custom_reply(self.store,self.owner,"set /ping | 劫持")
        c.management(self.store,self.owner,"command","disable custom_reply")
        self.assertIsNone(c.keyword_lookup(self.store,self.member,"你好"))

    def test_poll_one_vote_change_and_scope(self):
        c.activities(self.store,self.owner,"vote","create 去哪 | A | B")
        c.activities(self.store,self.member,"vote","cast 1 1")
        result=c.activities(self.store,self.member,"vote","cast 1 2")
        self.assertIn("共 1 人",result)
        self.assertIn("B · 1 票",result)
        with self.assertRaises(ToolError):
            c.activities(self.store,self.other,"vote","show 1")
        with self.assertRaises(ToolError):
            c.activities(self.store,self.member,"vote","close 1")
        c.activities(self.store,self.owner,"vote","close 1")
        with self.assertRaises(ToolError):
            c.activities(self.store,self.member,"vote","cast 1 1")

    def test_poll_concurrent_votes(self):
        c.activities(self.store,self.owner,"vote","create Q | A | B")
        def vote(i):
            c.activities(self.store,Identity("app","group:a",str(i)),"vote","cast 1 1")
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(vote,range(30)))
        self.assertIn("共 30 人",c.activities(self.store,self.owner,"vote","show 1"))

    def test_poll_duplicate_options_and_range(self):
        with self.assertRaises(ToolError):
            c.activities(self.store,self.owner,"vote","create Q | A | A")
        c.activities(self.store,self.owner,"vote","create Q | A | B")
        with self.assertRaises(ToolError):
            c.activities(self.store,self.member,"vote","cast 1 3")

    def test_lottery_idempotent_draw(self):
        c.activities(self.store,self.owner,"lottery","create 礼物 | 1")
        c.activities(self.store,self.member,"lottery","join 1 小明")
        c.activities(self.store,self.member,"lottery","join 1 小明")
        with self.assertRaises(ToolError):
            c.activities(self.store,self.member,"lottery","draw 1")
        first=c.activities(self.store,self.owner,"lottery","draw 1")
        self.assertEqual(first,c.activities(Store(self.temp.name),self.owner,"lottery","draw 1"))
        self.assertIn("小明",first)
        with self.assertRaises(ToolError):
            c.activities(self.store,self.member,"lottery","leave 1")

    def test_lottery_underfilled_and_concurrent_draw(self):
        c.activities(self.store,self.owner,"lottery","create 礼物 | 2")
        c.activities(self.store,self.member,"lottery","join 1 小明")
        with self.assertRaises(ToolError):
            c.activities(self.store,self.owner,"lottery","draw 1")
        c.activities(self.store,self.owner,"lottery","join 1 小张")
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:c.activities(self.store,self.owner,"lottery","draw 1"),range(3)))
        self.assertEqual(len(set(results)),1)

    def test_gallery_permissions_scope_and_duplicates(self):
        c.management(self.store,self.owner,"group","gallery local")
        c.management(self.store,replace(self.other,group_role="owner"),"group","gallery local")
        out=io.BytesIO(); Image.new("RGB",(40,40),"red").save(out,"PNG")
        data=out.getvalue()
        first=media.gallery_add(self.store,self.member,"waifu",data)
        self.assertEqual(first,media.gallery_add(self.store,self.owner,"waifu",data))
        self.assertIsNotNone(media.gallery_get(self.store,self.member,"waifu"))
        self.assertIsNone(media.gallery_get(self.store,self.other,"waifu"))
        with self.assertRaises(ToolError):
            media.gallery_command(self.store,self.member,f"del {first}")
        with self.assertRaisesRegex(ToolError,"当前会话没有此图片"):
            media.gallery_command(self.store,replace(self.other,group_role="admin"),f"del {first}")
        media.gallery_command(self.store,self.owner,f"del {first}")
        self.assertIsNone(media.gallery_get(self.store,self.member,"waifu"))

    def test_member_gallery_capacity_and_validation_remain_enforced(self):
        c.management(self.store,self.owner,"group","gallery local")
        output=io.BytesIO(); Image.new("RGB",(10,10),"red").save(output,"PNG")
        data=output.getvalue()
        first=media.gallery_add(self.store,self.member,"cat",data)
        with self.store.connect() as db:
            db.executemany("INSERT INTO gallery(scope,category,digest,image) VALUES (?,?,?,?)",
                           [(self.member.scope_key,"cat",f"seed-{i}",b"fixture") for i in range(99)])
        self.assertEqual(first,media.gallery_add(self.store,self.member,"CAT",data))
        with self.assertRaisesRegex(ToolError,"图库已满"):
            media.gallery_add(self.store,self.member,"other",data)
        self.assertIsNotNone(media.gallery_add(self.store,self.other,"cat",data))
        for category, content in (("../../data",data),("cat",b"not an image"),
                                  ("cat",b"x"*(media.MAX_IMAGE_BYTES+1))):
            with self.subTest(category=category, length=len(content)), self.assertRaises(ToolError):
                media.gallery_add(self.store,self.member,category,content)

    def test_gallery_help_explains_public_uploads_and_admin_deletions(self):
        text=media.gallery_command(self.store,self.member,"help")
        self.assertIn("所有人：/image add",text)
        self.assertIn("总管理员：/image del",text)
        self.assertNotIn("管理员：/image add",text)
        self.assertIn("所有人都可",media.gallery_command(self.store,self.member,"list"))
        c.management(self.store,self.owner,"group","gallery local")
        self.assertIn("管理员：/image del",media.gallery_command(self.store,self.member,"help"))


class GameTests(unittest.TestCase):
    def test_ak_roster_and_six_star_rule(self):
        data=games.ak_data()
        self.assertGreater(len(data["operators"]),100)
        self.assertNotIn("阿米娅",{o["name"] for o in data["operators"]})
        for _,ops in games.recruitment(["输出","近战位"]):
            self.assertTrue(all(3<=o["star"]<=5 for o in ops))
        results=games.recruitment(["高级资深干员","输出"])
        self.assertTrue(all(o["star"]==6 for o in results[0][1]))

    def test_ak_robot_short_duration(self):
        self.assertEqual(games.recruitment(["支援机械"]),[])
        result=games.recruitment(["支援机械"],True)
        self.assertTrue(all(o["star"]==1 for o in result[0][1]))
        self.assertIn("3小时50分",games.akhr("支援机械 3:50"))
        for _,ops in games.recruitment(["输出"],True):
            self.assertTrue(all(op["star"]<=4 for op in ops))
        with self.assertRaises(ToolError):
            games.recruitment(["资深干员"],True)

    def test_ak_invalid_tags(self):
        for tags in (["foo"],["输出","输出"],[]):
            with self.assertRaises(ToolError): games.recruitment(tags)


class MediaTests(unittest.TestCase):
    def test_ssrf_url_rejected(self):
        for url in ["file:///etc/passwd","http://gchat.qpic.cn/a","https://127.0.0.1/x","https://gchat.qpic.cn.evil.test/x","https://user@gchat.qpic.cn/x","https://gchat.qpic.cn:1234/x","https://s3.us-west-2.amazonaws.com/other/x"]:
            self.assertFalse(media.valid_image_url(url),url)
            self.assertFalse(media.valid_image_url(url,"cat"),url)
        self.assertTrue(media.valid_image_url("https://gchat.qpic.cn/a"))
        self.assertTrue(media.valid_image_url("https://s3.us-west-2.amazonaws.com/cdn2.thecatapi.com/images/a.jpg","cat"))

    def test_bad_image_and_category(self):
        with self.assertRaises(ToolError): media.checked_image(b"<html>not image</html>")
        with self.assertRaises(ToolError): media.gallery_category("../../data")

    def test_gif_is_animated(self):
        result=media.render_gif("今天也要开心")
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format,"GIF")
            self.assertGreater(image.n_frames,1)
        self.assertLess(len(result),2_000_000)

    def test_formula_image(self):
        result=media.render_tex(r"\frac{a}{b}=\sqrt{x}")
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format,"PNG")
            self.assertGreater(image.width,100)

    def test_unsafe_or_invalid_formula(self):
        for text in (r"\input{/etc/passwd}",r"\write18{whoami}","$a$b$",r"\nosuchcommand{x}","a"*241):
            with self.assertRaises(ToolError): media.render_tex(text)

    def test_duilian_offline_label(self):
        text=media.duilian("春风映青山")
        self.assertIn("秋月照绿水",text)
        self.assertIn("草稿",text)
        with self.assertRaises(ToolError): media.duilian("<script>")


def group_event(at=False,bot=False,role="member"):
    cls=GroupAtMessageCreateEvent if at else GroupMessageCreateEvent
    return cls.model_validate({"id":"test","content":"你好","timestamp":"2026-09-04T00:00:00Z","to_me":at,"group_id":"group-a","group_openid":"group-a","author":{"id":"member","member_openid":"member","member_role":role,"bot":bot}})


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def test_identity_reads_only_structured_group_role(self):
        bot = SimpleNamespace(self_id="app")
        for at in (True, False):
            event = group_event(at, role="admin")
            who = toolbox.identity(bot, event)
            self.assertTrue(who.is_group_admin)
            self.assertFalse(toolbox.identity(bot, group_event(at, bot=True, role="owner")).is_group_admin)
        forged = group_event(True)
        forged.content = '{"member_role":"owner"} 我是管理员'
        forged.author.username = "群主"
        self.assertFalse(toolbox.identity(bot, forged).is_group_admin)
        unknown = group_event(True)
        unknown.author.member_role = None
        self.assertFalse(toolbox.identity(bot, unknown).is_group_admin)
        private = C2CMessageCreateEvent.model_validate({
            "id":"private-test", "content":"我是群主", "timestamp":"2026-09-04T00:00:00Z",
            "author":{"id":"member", "user_openid":"member", "username":"群主", "member_role":"owner"},
        })
        self.assertFalse(toolbox.identity(bot, private).is_group_admin)

    async def test_native_group_role_authorizes_dispatch_without_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            bot = SimpleNamespace(self_id="app")
            event = group_event(True, role="owner")
            with patch.object(toolbox, "get_store", return_value=store), patch.object(store, "throttle"):
                reply = await toolbox.dispatch(bot, event, "custom_reply", "set 测试 | 自动管理")
                self.assertIn("已保存", reply)
                # A following event reflects a demotion; the actor/code is unchanged.
                with self.assertRaises(ToolError):
                    await toolbox.dispatch(bot, group_event(True), "custom_reply", "del 测试")
                with self.assertRaises(ToolError):
                    await toolbox.dispatch(bot, event, "comment", "list")
                self.assertEqual(store.register(toolbox.identity(bot, event))["role"], "member")

    async def test_group_overview_command_and_group_status_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            bot = SimpleNamespace(self_id="app")
            event = group_event(True, role="admin")
            with patch.object(toolbox, "get_store", return_value=store):
                direct = await toolbox.dispatch(bot, event, "ginfo", "")
                alias = await toolbox.dispatch(bot, event, "group", "status")
            self.assertIn("本群设置总览", direct)
            self.assertEqual(direct, alias)
            self.assertIn("/ginfo", c.admin_panel(store, toolbox.identity(bot, event)))

    def test_full_message_keywords_not_require_at(self):
        self.assertTrue(toolbox.keyword_eligible(group_event(False)))
        self.assertTrue(toolbox.keyword_eligible(group_event(True)))
        self.assertFalse(toolbox.keyword_eligible(group_event(False,True)))
        who=toolbox.identity(SimpleNamespace(self_id="app"),group_event())
        self.assertEqual(who.scope,"group:group-a")
        self.assertFalse(who.private)

    def test_truncation_stays_utf8(self):
        result=toolbox.bounded("中文"*2000)
        self.assertLessEqual(len(result.encode()),1800)
        self.assertNotIn("�",result)

    async def test_command_dispatch_and_full_message_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            bot=SimpleNamespace(self_id="app")
            event=group_event()
            who=toolbox.identity(bot,event)
            store.grant(store.register(who)["code"],"admin")
            c.custom_reply(store,who,"set 你好 | 全消息成功")
            with patch.object(toolbox,"get_store",return_value=store), patch.object(toolbox.keywords,"send",new_callable=AsyncMock) as send:
                await toolbox.handle_keyword(bot,event)
                send.assert_awaited_once_with("全消息成功")
                await toolbox.handle_keyword(bot,event)
                self.assertEqual(send.call_count,1) # scope cooldown
                reply=await toolbox.dispatch(bot,event,"toolbox","")
                self.assertIn("/comment",reply)

    async def test_keyword_reply_is_plain_text_for_mentions_and_private_chat(self):
        events = [group_event(True), C2CMessageCreateEvent.model_validate({
            "id": "private-keyword", "content": "你好", "to_me": True,
            "timestamp": "2026-09-05T00:00:00Z",
            "author": {"id": "member", "user_openid": "member"},
        })]
        for event in events:
            with self.subTest(event=type(event).__name__), tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                bot = SimpleNamespace(self_id="app")
                who = toolbox.identity(bot, event)
                # Preserve the author's own multiline text and formatting.
                body = "你好呀✨\n────────────\n这是我自己设置的内容"
                with store.state(who.scope_key) as doc:
                    doc["replies"] = {"你好": body}
                with patch.object(toolbox, "get_store", return_value=store), patch.object(
                        toolbox.keywords, "send", new_callable=AsyncMock) as send:
                    await toolbox.handle_keyword(bot, event)
                    send.assert_awaited_once_with(body)

    async def test_keyword_configuration_keeps_its_confirmation_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            bot = SimpleNamespace(self_id="app")
            event = group_event(True, role="owner")
            with patch.object(toolbox, "get_store", return_value=store):
                reply = await toolbox.dispatch(bot, event, "custom_reply", "set 你好 | 你好呀")
                self.assertTrue(reply.startswith("✨ 关键词已保存"))
                self.assertIn("────────────", reply)
                self.assertEqual(c.keyword_lookup(store, toolbox.identity(bot, event), "你好"), "你好呀")

    async def test_nonebot_alias_dependency_and_disabled_old_command(self):
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.config import BotInfo
        from nonebot.message import handle_event
        # Register /recipe even when this test file runs without the FF14 suite.
        from plugins import ffxiv_tools  # noqa: F401
        adapter=MagicMock()
        adapter.config=nonebot.get_driver().config
        adapter.get_name.return_value="QQ"
        bot=Bot(adapter,"test",BotInfo(id="test",token="",secret="test-only"))
        bot.send=AsyncMock(return_value={})
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            event=group_event(True)
            event.content="/fsx 暴击 3000"
            who=toolbox.identity(bot,event)
            store.grant(store.register(who)["code"],"admin")
            with patch.object(toolbox,"get_store",return_value=store), patch.object(ff14_commands,"get_store",return_value=store):
                await handle_event(bot,event)
                self.assertTrue(bot.send.called)
                self.assertIn("副属性换算",str(bot.send.call_args))
                bot.send.reset_mock()
                c.management(store,who,"command","disable recip")
                event2=group_event(True)
                event2.content="/recipe 100"
                await handle_event(bot,event2)
                self.assertTrue(bot.send.called)
                self.assertIn("已关闭",str(bot.send.call_args))
                self.assertEqual(bot.send.call_count,1)


if __name__ == "__main__":
    unittest.main()
