"""QQ-facing commands for the AGPL learning-chat port."""
from __future__ import annotations

import hashlib
import math

from message_ui import help_panel, panel

from .learning_chat import LearningContent, LearningStore, PAGE_SIZE, group_only
from .storage import Identity, Store, ToolError


def _number(value: str, low: int, high: int, usage: str) -> int:
    if not value.isascii() or not value.isdigit() or not low <= int(value) <= high:
        raise ToolError(usage)
    return int(value)


def _switch(value: str, usage: str) -> int:
    options = {"on": 1, "off": 0, "开": 1, "关": 0, "开启": 1, "关闭": 0}
    if value.lower() not in options:
        raise ToolError(usage)
    return options[value.lower()]


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / 1024 / 1024:.1f} MiB"


def help_text(learning: LearningStore, who: Identity) -> str:
    state = learning.settings(who)
    return help_panel("群聊学习 · " + ("已开启" if state["enabled"] else "已关闭"), [
        "开启后学习本群相邻发言，达到次数后概率回复。",
        "群主/管理员：/learn on 或 /learn off",
        "/learn threshold 4 · 回复需学会几次（1～10）",
        "/learn repeat 3 · 复读阈值（0 关闭，2～10）",
        "/learn image on 或 off · 是否学习图片",
        "/learn mode public 或 local · 群主设置公开共享/本群私有",
        "/learn word add 屏蔽词 · del 屏蔽词 · list",
        "/learn list [页码] · 查看已学习回复",
        "/learn ban 回复编号 · /learn bans [页码]",
        "/learn unban 禁用编号",
        "回复某条文字并发送 /learn ban-reply · 禁用该内容",
        "回复某位群友并发送 /learn user block 或 unblock",
        "群主/总管理员：/learn clear confirm · 清空学习数据",
    ], footer="默认关闭、公开库。公开库会让问答在其他公开群参与回复；本群私有不跨群。学习内容会被保存，请先告知群成员。")


def dispatch(learning: LearningStore, store: Store, who: Identity, raw: str,
             *, reply_text: str = "", reply_user: str = "") -> str:
    group_only(who)
    args = raw.strip().split()
    if not args or args[0].lower() in {"help", "帮助", "status", "状态"}:
        state = learning.settings(who)
        if not args or args[0].lower() in {"help", "帮助"}:
            return help_text(learning, who)
        return panel("群聊学习 · 状态", [
            "当前：" + ("已开启" if state["enabled"] else "已关闭"),
            f"回复阈值：{state['answer_threshold']} 次",
            "复读：" + (
                f"{'开启' if state.get('repeat_enabled', 1) else '关闭'} · "
                f"阈值 {state['repeat_threshold']} 次 · 概率 {state.get('repeat_probability', 100)}%"
                if state["repeat_threshold"] else "关闭"
            ),
            "打断复读：" + (
                f"{'开启' if state.get('interrupt_repeat_enabled', 1) else '关闭'} · "
                f"概率 {state.get('interrupt_repeat_probability', 25)}%"
            ),
            "图片学习：" + ("开启" if state["image_enabled"] else "关闭"),
            "学习库：" + ("公开共享" if state.get("library_mode", "public") == "public" else "本群私有"),
            f"已学习：{state['pairs']} 组回复 · {state['contents']} 个内容",
            f"图片：{state['images']}/100 个 · {_size(state['image_bytes'])}/100 MiB",
            f"已禁用回复：{state['bans']} · 屏蔽词：{state['blocked_words']} · 屏蔽成员：{state['blocked_users']}",
        ], footer="管理方法：/learn help。学习数据不会进入图片图库或 AI 角色上下文。")

    action = args[0].lower()
    if action in {"on", "off", "开", "关", "开启", "关闭"} and len(args) == 1:
        enabled = _switch(action, "用法：/learn on 或 /learn off")
        learning.update(who, "enabled", enabled)
        if enabled:
            return panel("群聊学习已开启", [
                "现在开始记录本群合规文字与图片，并学习相邻发言。",
                "达到回复阈值后才会概率回复，不会立刻变成话痨。",
                "本群数据不会给其他群使用。",
            ], footer="这会保存群聊内容，请告知群成员。随时 /learn off 停止继续学习和回复，已有数据会保留。")
        return panel("群聊学习已关闭", "停止记录和自动回复；已有学习数据保留。", footer="群主可用 /learn clear confirm 永久清空本群学习内容。")
    if action in {"threshold", "阈值"} and len(args) == 2:
        value = _number(args[1], 1, 10, "用法：/learn threshold 4，范围 1～10。")
        learning.update(who, "answer_threshold", value)
        return panel("回复阈值已更新", f"同一回答至少学习 {value} 次后，才可能被选中回复。")
    if action in {"repeat", "复读"} and len(args) == 2:
        value = _number(args[1], 0, 10, "用法：/learn repeat 3；0 关闭，或设置 2～10。")
        if value == 1:
            raise ToolError("复读阈值不能设为 1；请填写 0（关闭）或 2～10。")
        learning.update(who, "repeat_threshold", value)
        return panel("复读设置已更新", "复读已关闭。" if value == 0 else f"至少 {value} 条相同消息且来自两人后，机器人才可能复读。")
    if action in {"image", "图片"} and len(args) == 2:
        value = _switch(args[1], "用法：/learn image on 或 /learn image off")
        learning.update(who, "image_enabled", value)
        return panel("图片学习设置已更新", "会学习合规 QQ 图片。" if value else "以后不再学习新图片；已学习图片仍可能回复。", footer="每群最多 100 个学习图片、合计 100 MiB；单图仍受 4 MiB 安全限制。")
    if action in {"mode", "模式"} and len(args) == 2:
        modes = {"public": "public", "公开": "public", "local": "local", "private": "local", "私有": "local", "本群": "local"}
        mode = modes.get(args[1].lower())
        if mode is None:
            raise ToolError("用法：/learn mode public（公开共享）或 /learn mode local（本群私有）")
        learning.update(who, "library_mode", mode)
        if mode == "public":
            return panel("学习库已设为公开", "本群学到的问答可在其他公开群参与回复，本群也可使用其他公开群的问答。", footer="切换不会删除已有内容。")
        return panel("学习库已设为本群私有", "本群只学习和使用自己的问答，不再使用其他群的公开问答。", footer="切换不会删除已有内容。")
    if action in {"word", "屏蔽词"}:
        if len(args) == 2 and args[1].lower() in {"list", "列表"}:
            words = learning.words(who)
            return panel("学习屏蔽词", words or ["本群尚未设置。"], footer="添加：/learn word add 词 · 删除：/learn word del 词")
        if len(args) == 3 and args[1].lower() in {"add", "添加"}:
            learning.block_word(who, args[2])
            return panel("学习屏蔽词已添加", f"含“{args[2]}”的新消息不再学习或触发回复。")
        if len(args) == 3 and args[1].lower() in {"del", "删除"}:
            learning.unblock_word(who, args[2])
            return panel("学习屏蔽词已删除", f"“{args[2]}”不再被此规则拦截。")
        raise ToolError("用法：/learn word add 词、/learn word del 词、/learn word list")
    if action in {"list", "列表"}:
        page = _number(args[1], 1, 1000, "用法：/learn list [页码]") if len(args) == 2 else 1
        if len(args) > 2:
            raise ToolError("用法：/learn list [页码]")
        rows, total = learning.pairs(who, page)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        if page > pages:
            raise ToolError(f"页码超出范围，当前共 {pages} 页。")
        lines = [f"#{row['id']} · 学习 {row['count']} 次\n{row['prompt_preview']} → {row['reply_preview']}" for row in rows]
        return panel("本群已学习回复", lines or ["还没有达到可列出的学习记录。"], subtitle=f"第 {page}/{pages} 页", footer="禁用某个回复：/learn ban 回复编号")
    if action in {"ban", "禁用"} and len(args) == 2:
        pair_id = _number(args[1], 1, 2_147_483_647, "用法：/learn ban 回复编号")
        result = learning.ban_pair(who, pair_id)
        return panel("学习回复已禁用", [f"禁用编号 #{result['id']}", result["preview"]], footer="同样内容不会再作为本群的学习回复；恢复：/learn unban 禁用编号")
    if action in {"ban-reply", "禁用回复"} and len(args) == 1:
        content = LearningContent.from_text(reply_text)
        if content is None:
            raise ToolError("请回复一条普通文字消息，再发送 /learn ban-reply。")
        result = learning.ban_content(who, content)
        return panel("被回复内容已禁用", [f"禁用编号 #{result['id']}", result["preview"]], footer="该内容不会再作为本群的学习回复。")
    if action in {"bans", "禁用列表"}:
        page = _number(args[1], 1, 1000, "用法：/learn bans [页码]") if len(args) == 2 else 1
        if len(args) > 2:
            raise ToolError("用法：/learn bans [页码]")
        rows, total = learning.bans(who, page)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        if page > pages:
            raise ToolError(f"页码超出范围，当前共 {pages} 页。")
        return panel("本群禁用回复", [f"#{row['id']} · {row['preview']}" for row in rows] or ["本群没有禁用回复。"], subtitle=f"第 {page}/{pages} 页", footer="恢复：/learn unban 禁用编号")
    if action in {"unban", "恢复"} and len(args) == 2:
        ban_id = _number(args[1], 1, 2_147_483_647, "用法：/learn unban 禁用编号")
        learning.unban(who, ban_id)
        return panel("学习回复已恢复", "已有匹配记录可以再次参与本群回复。")
    if action in {"user", "成员"} and len(args) == 2 and args[1].lower() in {"block", "unblock", "屏蔽", "恢复"}:
        if not reply_user:
            raise ToolError("请回复目标群友的一条消息，再发送 /learn user block 或 /learn user unblock。")
        actor = Identity(who.bot, who.scope, reply_user).actor
        if args[1].lower() in {"block", "屏蔽"}:
            learning.block_user(who, actor)
            return panel("已停止学习该成员", "之后不会记录该成员的新消息，也不会由其消息触发学习回复。", footer="机器人不显示或保存对方 QQ 号，只保存本群内的匿名身份摘要。")
        learning.unblock_user(who, actor)
        return panel("已恢复学习该成员", "之后该成员的新消息可再次参与本群学习。")
    if action in {"clear", "清空"}:
        if args != [args[0], "confirm"] and args != [args[0], "确认"]:
            raise ToolError("永久清空本群学习数据请发送：/learn clear confirm（仅群主/总管理员）")
        learning.clear(who)
        return panel("本群学习数据已清空", "学习内容、图片、回复关系和禁用回复已删除，设置与屏蔽词保留。", footer="此操作无法从机器人内撤销；备份中可能仍有旧副本。")
    raise ToolError("没有这个学习操作。发送 /learn help 查看完整用法。")
