from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

from message_ui import help_panel, panel
from .storage import Identity, Store, ToolError, clean
from . import gallery

CST = timezone(timedelta(hours=8))
PROTECTED = {"ping", "toolbox", "bot", "group", "command", "left_reply", "comment"}
ALIASES = {"recipe": "recip", "reid": "raid", "dpscheck": "dps", "mitem": "market", "天气": "weather", "trickcal": "tr"}
MANAGED = {"ai", "learn", "tr", "bili", "ff14", "fsx", "ofish", "hunt", "custom_reply", "vote", "lottery", "cat", "waifu", "gif", "image", "tex", "duilian", "akhr", "quest", "search", "market", "gather", "sales", "recip", "craftcost", "cheapest", "luck", "house", "weather", "dps", "raid", "dice", "random", "gate", "fflogs", "otter", "about"}
NEW_METERED = {"tr", "fsx", "ofish", "hunt", "custom_reply", "vote", "lottery", "cat", "waifu", "gif", "image", "tex", "duilian", "akhr"}
COMMAND_GROUPS = {
    "ff14": frozenset({
        "ff14", "market", "sales", "cheapest", "recip", "craftcost", "gather",
        "dps", "raid", "fflogs", "fsx", "quest", "search", "weather", "house",
        "ofish", "hunt", "luck", "gate",
    }),
}


def integer(value: str, low: int = 1, high: int = 1000000) -> int:
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ToolError("请输入有效整数。") from None
    if not low <= number <= high:
        raise ToolError(f"数字范围应为 {low}～{high}。")
    return number


def stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%m-%d %H:%M")


def quota(doc: dict, actor: str, consume: bool = False, now: float | None = None) -> tuple[int, int]:
    day = datetime.fromtimestamp(time.time() if now is None else now, CST).strftime("%Y-%m-%d")
    usage = doc.setdefault("usage", {"day": day, "users": {}})
    if usage["day"] != day:
        usage = doc["usage"] = {"day": day, "users": {}}
    limit = doc.get("quota", 100)
    used = usage["users"].get(actor, 0)
    if consume:
        if used >= limit:
            raise ToolError("今日工具箱次数已用完，次日 00:00（北京时间）恢复。")
        usage["users"][actor] = used + 1
        used += 1
    return used, limit


def _disabled_by(command: str, disabled: list[str]) -> str:
    disabled_set = set(disabled)
    if command in disabled_set:
        return command
    for group, members in COMMAND_GROUPS.items():
        if group in disabled_set and command in members:
            return group
    return ""


def _disabled_error(command: str, blocker: str) -> ToolError:
    if blocker in COMMAND_GROUPS:
        return ToolError(f"当前会话已关闭 {blocker.upper()} 插件，/{command} 暂不可用；管理员可用 /command enable {blocker} 恢复。")
    return ToolError(f"当前会话已关闭 /{command}，管理员可用 /command enable {command} 恢复。")


def gate(store: Store, who: Identity, command: str, consume: bool = False):
    command = ALIASES.get(command, command)
    if command in PROTECTED or command not in MANAGED:
        return
    if not consume or command not in NEW_METERED:
        blocker = _disabled_by(command, store.document(who.scope_key).get("disabled", []))
        if blocker:
            raise _disabled_error(command, blocker)
        return
    with store.state(who.scope_key) as doc:
        blocker = _disabled_by(command, doc.get("disabled", []))
        if blocker:
            raise _disabled_error(command, blocker)
        if consume and command in NEW_METERED:
            quota(doc, who.actor, True)


def admin_panel(store: Store, who: Identity) -> str:
    if not store.is_admin(who):
        raise ToolError("无权访问机器人管理面板。")
    role = store.role(who)
    if role == "owner":
        label = "机器人总管理员（服务器授权）"
    elif who.is_group_admin:
        label = "QQ群主（仅本群）" if who.group_role == "owner" else "QQ管理员（仅本群）"
    else:
        label = "本会话机器人管理员（手动授权）"
    lines = [f"当前权限：{label}", "你可以使用："]
    if role == "owner" and who.private:
        lines += ["全局反馈 · /comment list [页码]", "/comment show 编号 · 查看全文", "/comment done 编号 · 标记已处理", "/comment closed [页码] · 已处理列表"]
    lines += [
        "当前会话管理：" if who.private else "本群管理：" if who.scope.startswith("group:") else "当前频道管理：",
        "关键词 · /custom_reply set 关键词 | 内容", "/custom_reply list · /custom_reply del 关键词",
        "命令开关 · /command enable/disable 命令",
        "狩猎默认小区 · /group server 小区名", "每日配额 · /group quota 次数",
        "狩猎 · /hunt rule · /hunt kill · /hunt undo",
        "结束投票 · /vote close 编号", "抽奖开奖 · /lottery draw 编号",
    ]
    if not who.private and who.scope.startswith("group:"):
        lines.append("群设置总览 · /ginfo [页码]")
    target = gallery.current(store, who)
    if not target.public or role == "owner":
        lines.append("图库管理 · /image list [分类] · /image del 编号")
    if gallery.can_switch(store, who):
        lines.append("图库切换 · /group gallery public 或 local")
    if role == "owner":
        lines.append("总管理员专属 · /bot status · 运行状态")
        if who.private:
            lines.append("AI 总开关 · /ai 全局 on 或 off")
    if not who.private and who.scope.startswith("group:") and store.is_admin(who):
        lines.append("群聊学习 · /learn · 分群学习、复读与内容管理")
        lines.append("AI 角色 · /ai 暂停 编号 · /ai 恢复 编号 · /ai 清空 编号")
        lines.append("移除角色 · /ai 移除 编号")
        if who.group_role == "owner" or store.is_admin(who, owner=True):
            lines.append("批量移除 · /ai 移除 all")
    return panel("我的管理功能", lines, footer="会话设置仅影响当前会话；公共图删除仅限总管理员。具体参数请查看命令帮助。")


def management(store: Store, who: Identity, name: str, raw: str) -> str:
    args = raw.split()
    if name == "bot":
        if args and args[0] == "whoami" and len(args) > 1:
            if not who.private:
                raise ToolError("授权码仅能在机器人私聊使用，群内不会兑换。")
            if len(args) != 2:
                raise ToolError("授权码格式不正确。")
            record = store.redeem_admin_token(who, args[1])
            return panel("私聊身份已验证 · 等待服务器确认", [
                f"身份码：{record['code']}", "请在 15 分钟内回到服务器执行：",
                f"Windows：.\\bot.cmd admin add {record['code']}",
                f"Linux：./bot.sh admin add {record['code']}",
            ], footer="授权码已作废；此步骤不会新增权限。只确认你本人私聊获得的身份码。")
        if not store.is_admin(who):
            raise ToolError("无权访问机器人管理面板。")
        if not args or args == ["whoami"]:
            return admin_panel(store, who)
        if args == ["status"]:
            store.require_admin(who, owner=True)
            return panel("工具箱运行正常", ["本地 SQLite 持久化已启用", "反馈仅本地保存", "功能目录：/toolbox", "我的管理功能：/bot"])
        usage = "用法：/bot 或 /bot whoami"
        if store.is_admin(who, owner=True):
            usage += "，/bot status 查看运行状态"
        raise ToolError(usage)
    if name == "comment":
        if not args or args == ["help"]:
            return help_panel("意见反馈", ["/comment 你遇到的问题", "仅保存在本机器人服务器，总管理员可查看。", "请勿提交密码、Token 等敏感信息。", "总管理员私聊：/comment list [页码]", "/comment show 编号（全文）", "/comment done 编号", "/comment closed [页码]"])
        if args[0] in {"list", "closed"}:
            if len(args) > 2:
                raise ToolError("用法：/comment list [页码]")
            page = integer(args[1], 1, 2500) if len(args) == 2 else 1
            rows = store.feedback_list(who, page, args[0] == "closed")
            return panel("反馈箱 · " + ("已处理" if args[0] == "closed" else "待处理"), [f"第 {page} 页", *[f"#{r['id']} · {stamp(r['created'])}\n{r['body'][:85]}{'…' if len(r['body'])>85 else ''}" for r in rows]] if rows else ["本页没有反馈。"], footer="/comment show 编号 看全文；done 编号 标记已处理。")
        if args[0] == "show":
            store.require_inbox(who)
            if len(args) != 2:
                raise ToolError("用法：/comment show 编号")
            with store.connect() as db:
                row = db.execute("SELECT * FROM feedback WHERE id=?", (integer(args[1]),)).fetchone()
            if not row:
                raise ToolError("没有这条反馈。")
            return panel(f"反馈 #{row['id']}", row["body"], subtitle=stamp(row["created"])+(" · 已处理" if row["closed"] else " · 待处理"))
        if args[0] == "done":
            if len(args) != 2:
                raise ToolError("用法：/comment done 编号")
            store.feedback_close(who, integer(args[1]))
            return panel("反馈已处理", f"#{args[1]} 已归入已处理列表。")
        item_id = store.feedback_add(who, raw)
        return panel("反馈已保存", f"编号 #{item_id}，管理员可在私聊反馈箱查看。", footer="没有发送到外部服务。")
    if name in {"group", "command"}:
        store.require_admin(who)
    with store.state(who.scope_key) as doc:
        if name == "left_reply":
            used, limit = quota(doc, who.actor)
            return panel("今日工具箱用量", [f"已用 {used} / {limit} 次", f"剩余 {max(0, limit-used)} 次"], footer="仅统计新版工具箱非帮助请求（含失败请求）；不代表腾讯消息额度。北京时间零点重置。")
        if name == "group":
            if not args:
                lines = [f"狩猎默认小区：{doc.get('server', '未设置')}", f"工具箱日限额：{doc.get('quota', 100)} / 人",
                         "当前图库：" + gallery.target_from_doc(who, doc).label,
                         "/group server 梦羽宝境", "/group quota 100", "我的管理功能：/bot"]
                if gallery.can_switch(store, who):
                    lines.append("/group gallery public 或 local")
                return panel("当前会话设置", lines, footer="图库切换仅群主/总管理员可用；设置立即生效。")
            store.require_admin(who)
            if args[0] == "gallery":
                if not gallery.can_switch(store, who):
                    raise ToolError("图库模式只能在 QQ 群内由本群群主或服务器授权的机器人总管理员切换；私聊固定使用公共图库。")
                modes = {"public":"public", "公共":"public", "local":"local", "本群":"local", "本地":"local"}
                if len(args) != 2 or args[1] not in modes:
                    raise ToolError("用法：/group gallery public（公共）或 /group gallery local（本群）")
                doc["gallery_mode"] = modes[args[1]]
                public = doc["gallery_mode"] == "public"
                return panel("图库模式已切换", ["当前：" + ("公共图库" if public else "本会话图库"),
                    "后续上传将公开给使用公共图库的群；公共图仅总管理员可删除。" if public else "后续上传仅当前会话可用；所有分类合计最多 100 张。",
                    "原图库图片保留，不会自动搬迁或公开。"], footer="/image 查看帮助 · /image status 查看容量")
            if args[0] == "server" and len(args) == 2:
                doc["server"] = clean(args[1], 30)
                return panel("设置已保存", "狩猎默认小区：" + doc["server"])
            if args[0] == "quota" and len(args) == 2:
                doc["quota"] = integer(args[1], 1, 10000)
                return panel("设置已保存", f"工具箱每人每天 {doc['quota']} 次，立即生效。")
            # Role writes use a separate SQLite transaction; perform after state context exits.
            if len(args) == 3 and args[:2] in (["admin", "add"], ["admin", "remove"]):
                store.require_admin(who, owner=True)
            else:
                raise ToolError("用法：/group server 小区名 | /group quota 次数；群主可用 /group gallery public 或 local 切换图库。")
        elif name == "command":
            if not args or args == ["list"]:
                disabled = doc.get("disabled", [])
                return panel("功能开关", ["本群已关闭：" + ("、".join("/" + item for item in disabled) or "无"), "关闭示例：/command disable cat", "恢复示例：/command enable cat", "可选功能：" + " / ".join(sorted(MANAGED))], footer="把示例中的 cat 换成要控制的功能名；仅当前会话生效，管理入口和 /ping 不可关闭。")
            store.require_admin(who)
            if len(args) != 2 or args[0] not in {"enable", "disable"}:
                raise ToolError("用法：/command enable/disable 命令名")
            target = ALIASES.get(args[1].lstrip("/"), args[1].lstrip("/"))
            if target not in MANAGED:
                raise ToolError("该命令不在可管理列表，或属于受保护的管理命令。")
            disabled = set(doc.get("disabled", []))
            (disabled.add if args[0] == "disable" else disabled.discard)(target)
            doc["disabled"] = sorted(disabled)
            return panel("命令开关已更新", f"/{target} 已{'关闭' if args[0]=='disable' else '开启'}。")
    # Never nest two writers, even when changing role from a group command.
    if name == "group":
        store.grant(args[2], "admin" if args[1] == "add" else "member", who.scope_key)
        return panel("手动授权已更新", "仅对当前会话生效。", footer="移除的是额外授权；实际 QQ 群角色仍以平台提供的当前身份为准。")
    raise ToolError("未知管理命令。")


def custom_reply(store: Store, who: Identity, raw: str) -> str:
    args = raw.split(maxsplit=1)
    if not args:
        return help_panel("关键词回复", ["/custom_reply set 关键词 | 回复内容", "/custom_reply list", "/custom_reply del 关键词", "有群全消息权限：直接发送关键词", "无全消息权限：@机器人 关键词"], footer="精确匹配 QQ 平台实际推送的消息；本地设置不能代替平台全消息权限。")
    with store.state(who.scope_key) as doc:
        replies = doc.setdefault("replies", {})
        if args == ["list"]:
            return panel("当前会话的关键词", list(replies) or ["尚未设置。"])
        store.require_admin(who)
        if len(args) == 2 and args[0] == "set":
            parts = args[1].split("|", 1)
            if len(parts) != 2:
                raise ToolError("请用 | 分隔关键词和回复。")
            key, value = clean(parts[0], 30), clean(parts[1], 400)
            if key.startswith("/"):
                raise ToolError("关键词不能以 / 开头，避免覆盖命令。")
            if key not in replies and len(replies) >= 50:
                raise ToolError("每个会话最多 50 个关键词。")
            replies[key] = value
            return panel("关键词已保存", f"发送“{key}”即可触发；无全消息权限时请 @机器人。")
        if len(args) == 2 and args[0] == "del":
            if not replies.pop(args[1].strip(), None):
                raise ToolError("没有这个关键词。")
            return panel("关键词已移除", "此关键词不再自动回复。")
        raise ToolError("用法：/custom_reply set 关键词 | 内容，或 list / del 关键词")


def keyword_lookup(store: Store, who: Identity, text: str) -> str | None:
    doc = store.document(who.scope_key)
    if doc:
        if "custom_reply" in doc.get("disabled", []):
            return None
        return doc.get("replies", {}).get(text.strip())


def activities(store: Store, who: Identity, kind: str, raw: str) -> str:
    args = raw.split()
    title = "投票" if kind == "vote" else "报名抽奖"
    if not args or args == ["help"]:
        lines = (["/vote create 今晚玩什么 | 海钓 | 零式", "/vote cast 编号 选项号", "/vote show 编号", "/vote close 编号"] if kind == "vote" else ["/lottery create 周末礼物 | 2", "/lottery join 编号 昵称", "/lottery leave 编号", "/lottery show 编号", "/lottery draw 编号"])
        return help_panel(title, [*lines, f"/{kind} list"], footer="仅本会话有效；发起人或管理员结束。抽奖只从主动报名的人中抽取。")
    with store.state(who.scope_key) as doc:
        events = doc.setdefault(kind, {})
        if args[0] == "create":
            if len(events) >= 100:
                raise ToolError("本会话活动记录已达 100 条，请联系服务器管理员归档。")
            if sum(not e["closed"] for e in events.values()) >= 10:
                raise ToolError("最多同时开启 10 个活动，请先结束旧活动。")
            payload_text = raw.split(maxsplit=1)[1] if len(args)>1 else ""
            parts = [clean(p, 60) for p in payload_text.split("|")]
            if kind == "vote":
                if not 3 <= len(parts) <= 9 or len(set(parts[1:])) != len(parts)-1:
                    raise ToolError("投票需要 2～8 个不重复选项，用 | 分隔。")
                payload = {"options": parts[1:], "votes": {}}
            else:
                if len(parts) != 2:
                    raise ToolError("用法：/lottery create 标题 | 中奖人数")
                payload = {"count": integer(parts[1], 1, 10), "members": {}, "winners": []}
            event_id = str(max([int(i) for i in events] or [0])+1)
            events[event_id] = {"title":parts[0],"owner":who.actor,"closed":False,"created":time.time(),**payload}
            return panel(f"{title}已创建 · #{event_id}", [parts[0], (f"/vote cast {event_id} 选项号" if kind == "vote" else f"/lottery join {event_id} 昵称"), *([f"{i}. {p}" for i,p in enumerate(parts[1:],1)] if kind == "vote" else [f"中奖人数：{payload['count']}"])])
        if args == ["list"]:
            return panel(title+"列表", [f"#{i} {'已结束' if e['closed'] else '进行中'} · {e['title']}" for i,e in list(events.items())[-10:]] or ["暂无活动。"])
        if len(args) < 2 or args[1] not in events:
            raise ToolError("当前会话没有此活动编号。")
        e = events[args[1]]
        op = args[0]
        if op in {"close", "draw"}:
            if e["owner"] != who.actor:
                store.require_admin(who)
            if kind == "vote" and op == "close" and len(args) == 2:
                e["closed"] = True
            elif kind == "lottery" and op == "draw" and len(args) == 2:
                if not e["closed"]:
                    if len(e["members"]) < e["count"]:
                        raise ToolError("报名人数少于中奖人数，暂时不能开奖。")
                    e["winners"] = secrets.SystemRandom().sample(list(e["members"]), e["count"])
                    e["closed"] = True
            else:
                raise ToolError("投票使用 close，抽奖使用 draw。")
        elif op in {"cast", "join", "leave"}:
            if e["closed"]:
                raise ToolError("活动已结束，不能再修改。")
            if kind == "vote" and op == "cast" and len(args) == 3:
                option = integer(args[2], 1, len(e["options"]))-1
                if who.actor not in e["votes"] and len(e["votes"]) >= 1000:
                    raise ToolError("本场参与人数已满。")
                e["votes"][who.actor] = option
            elif kind == "lottery" and op == "join" and len(args) >= 3:
                if who.actor not in e["members"] and len(e["members"]) >= 1000:
                    raise ToolError("本场报名人数已满。")
                nickname = clean(" ".join(args[2:]), 20)
                if any(a != who.actor and n == nickname for a,n in e["members"].items()):
                    raise ToolError("昵称已被本场其他报名者使用，请换一个。")
                e["members"][who.actor] = nickname
            elif kind == "lottery" and op == "leave" and len(args) == 2:
                e["members"].pop(who.actor, None)
            else:
                raise ToolError(f"参数不正确，发送 /{kind} 查看帮助。")
        elif op != "show" or len(args) != 2:
            raise ToolError(f"未知操作，发送 /{kind} 查看帮助。")
        if kind == "vote":
            total = len(e["votes"])
            lines = [f"{i+1}. {option} · {list(e['votes'].values()).count(i)} 票" for i,option in enumerate(e["options"])]
            lines.append(f"共 {total} 人参与 · 每人一票，可修改")
        else:
            lines = [f"报名 {len(e['members'])} 人 · 抽取 {e['count']} 人"]
            if e["closed"]:
                lines.extend(["中奖名单：", *[f"🎉 {e['members'][a]}" for a in e["winners"]]])
            elif op == "join":
                lines.append(f"报名成功：{e['members'][who.actor]}")
            elif op == "leave":
                lines.append("你已退出报名。")
        return panel(f"{e['title']} · #{args[1]}", lines, subtitle="已结束" if e["closed"] else "进行中", footer="结果保存在服务器，重复开奖不会重抽。" if kind == "lottery" else "仅本会话可查看；不公开投票人的身份。")


def hunt(store: Store, who: Identity, raw: str) -> str:
    args = raw.split()
    if not args:
        return help_panel("手动狩猎时钟", ["管理员设置规则：/hunt rule 怪物 最早小时 最晚小时", "示例：/hunt rule 测试怪 4 6", "/hunt kill 怪物 [服务器] [已过去的分钟]", "/hunt check 怪物 [服务器]", "/hunt list [服务器]", "/hunt undo 怪物 [服务器]", "/group server 梦羽宝境"], footer="窗口由管理员设置，不包含自动通报、触发条件或维护重置；过窗不等于已刷新。")
    with store.state(who.scope_key) as doc:
        rules = doc.setdefault("hunt_rules", {})
        kills = doc.setdefault("hunt_kills", {})
        op = args[0]
        if op == "rule":
            store.require_admin(who)
            if len(args) != 4:
                raise ToolError("用法：/hunt rule 怪物 最早小时 最晚小时")
            name = clean(args[1], 40)
            try:
                early, late = float(args[2]), float(args[3])
            except ValueError:
                raise ToolError("小时必须是数字。") from None
            if not (0 < early <= late <= 720):
                raise ToolError("需要满足 0 < 最早 ≤ 最晚 ≤ 720 小时。")
            if name not in rules and len(rules) >= 100:
                raise ToolError("规则已满（最多 100 个）。")
            rules[name] = [early, late]
            return panel("狩猎规则已保存", f"{name} · 击杀后 {early:g}～{late:g} 小时窗口", footer="这是管理员手动设定的范围，不是官方刷新保证。")
        if op == "list":
            if len(args) > 2:
                raise ToolError("用法：/hunt list [服务器]")
            server = args[1] if len(args) == 2 else doc.get("server", "")
            rows = [v for v in kills.values() if v["server"] == server and v["times"]]
            return panel("狩猎记录 · "+server, [f"{v['name']} · 最近击杀 {stamp(v['times'][-1])}" for v in rows[:12]] or ["没有击杀记录；先设置默认服务器和狩猎规则。"], footer="/hunt check 怪物 服务器 查看窗口。")
        if op not in {"kill", "check", "undo"} or not 2 <= len(args) <= (4 if op == "kill" else 3):
            raise ToolError("发送 /hunt 查看用法。")
        name = clean(args[1], 40)
        server = args[2] if len(args) >= 3 else doc.get("server", "")
        if not server:
            raise ToolError("请提供服务器，或先 /group server 服务器。")
        server = clean(server, 30)
        if name not in rules:
            raise ToolError("尚未设置此怪物的窗口规则。管理员请先 /hunt rule 怪物 最早小时 最晚小时")
        key = name + "|" + server
        if key not in kills and len(kills) >= 500:
            raise ToolError("此会话狩猎记录已满。")
        record = kills.setdefault(key, {"name":name,"server":server,"times":[]})
        if op in {"kill", "undo"}:
            store.require_admin(who)
            if op == "kill":
                minutes = integer(args[3], 0, 43200) if len(args) == 4 else 0
                record["times"].append(time.time()-minutes*60)
                record["times"] = record["times"][-20:]
            elif record["times"]:
                record["times"].pop()
                return panel("已撤销上一条击杀记录", f"{name} · {server}", footer="可查询上一条记录；最多保留最近 20 次。")
        if not record["times"]:
            raise ToolError("尚无击杀记录。")
        last = record["times"][-1]
        early, late = [last+h*3600 for h in rules[name]]
        status = "窗口未到" if time.time()<early else ("窗口内" if time.time()<=late else "已过窗口上限；需确认是否漏记")
        return panel(f"{name} · {server}", [status, f"最后击杀：{stamp(last)}", f"估算窗口：{stamp(early)} ～ {stamp(late)}"], footer="北京时间；仅根据本群手动记录估算，不代表怪物已出现。")
