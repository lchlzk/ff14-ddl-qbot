"""Human-facing configuration commands; never include keys in any response."""
from __future__ import annotations

from message_ui import help_panel, panel
from .ai_provider import model_for, model_options, provider_for
from .ai_store import AIStore, DEFAULT_DAILY, number, private_only
from .storage import Identity, ToolError


ALIASES = {
    "help": "help", "帮助": "help", "设置": "setup", "setup": "setup",
    "服务": "service", "service": "service", "密钥": "key", "key": "key",
    "模型": "model", "model": "model",
    "角色": "role", "role": "role", "额度": "limit", "limit": "limit",
    "新建": "new", "new": "new", "角色列表": "roles", "roles": "roles",
    "选择": "select", "select": "select", "删除角色": "delete-role", "delete-role": "delete-role",
    "复用密钥": "copy-key", "copy-key": "copy-key",
    "发布": "publish", "publish": "publish", "加入": "join", "接入": "join", "join": "join",
    "确认": "confirm", "confirm": "confirm", "启用": "accept", "accept": "accept",
    "授权列表": "grants", "grants": "grants", "撤销": "revoke", "revoke": "revoke",
    "群设置": "group-settings", "group-settings": "group-settings",
    "删除密钥": "delete-key", "delete-key": "delete-key", "用量": "usage", "usage": "usage",
    "清空": "clear", "clear": "clear", "暂停": "pause", "pause": "pause",
    "恢复": "resume", "resume": "resume", "移除": "remove", "remove": "remove",
    "全局": "global", "global": "global", "状态": "status", "status": "status",
}


def _trigger_text(row: dict) -> str:
    trigger = "必须提到角色名" if row["mention_only"] else "无需点名"
    if row["collect_min"] == row["collect_max"] == 0:
        collect = "0（每条符合条件的消息都回复）"
    elif row["collect_min"] == row["collect_max"]:
        collect = f"{row['collect_min']} 条"
    else:
        collect = f"{row['collect_min']}～{row['collect_max']} 条随机"
    return f"{trigger} · 收集 {collect}"


def _group_settings_panel(row: dict) -> str:
    high_cost = not row["mention_only"] and row["collect_min"] == row["collect_max"] == 0
    warning = (
        "当前为无需点名且收集 0：群里每条普通消息都会尝试调用 AI，可能很快耗尽 Token、余额和每日额度。"
        if high_cost else
        "收集范围越小，调用越频繁；范围越大，每次携带的群消息越多，单次输入 Token 通常越高。"
    )
    return panel("AI 角色 · 群聊设置", [
        f"角色：{row['name']} · 授权编号 {row['id']}",
        f"目标群标识：{row['group_tag']}",
        "当前：" + _trigger_text(row),
        "点名开关：/ai 群设置 授权编号 点名 on 或 off",
        "收集范围：/ai 群设置 授权编号 收集 3 5",
        "每条回复：/ai 群设置 授权编号 收集 0",
    ], footer=warning + " 请使用专用限额 Key，并设置合适的 /ai 额度。")


def _model_panel(ai: AIStore, who: Identity) -> str:
    private_only(who)
    profile = ai.profile(who)
    if not profile:
        raise ToolError("请先用 /ai 新建 名字 | 人设 创建角色，再选择服务和模型。")
    current = model_for(profile["provider"], profile["region"], profile["model"])
    options = model_options(profile["provider"], profile["region"])
    lines = [f"当前：{current.code} · {current.label}"]
    for option in options:
        marks = []
        if option.recommended:
            marks.append("默认推荐")
        if option.code == current.code:
            marks.append("当前")
        suffix = "（" + "、".join(marks) + "）" if marks else ""
        lines.append(f"{option.code}{suffix}\n{option.reason}")
    provider = provider_for(profile["provider"], profile["region"])
    return panel(
        "AI 角色 · 可选模型", lines, subtitle=provider.label,
        footer="切换：/ai 模型 模型ID；恢复推荐：/ai 模型 推荐。手动切换保留 Key 和群授权，但会清空当前角色的聊天记忆。不会自动切换或重试。",
    )


def help_text(private: bool) -> str:
    if private:
        return help_panel("AI 角色 · 我的角色", [
            "每个人都能创建，不需要管理员权限。",
            "① /ai 新建 小桃 | 你是温柔活泼的猫娘，喜欢简短聊天",
            "② /ai 服务 glm（智谱）或 qwen beijing（千问）",
            "③ /ai 模型 查看并选择；不选则使用默认推荐",
            "④ /ai 密钥 你的APIKey（只在本私聊输入）",
            "说“小桃你好”即可试聊，所有角色共用每日 5 次。",
            "",
            "进群：/ai 发布 100 → 按提示加入群并私聊确认",
            "多角色：/ai 角色列表 · /ai 选择 角色编号",
            "编辑当前角色：/ai 角色 名字 | 人设",
            "管理：/ai 状态 · /ai 额度 次数 · /ai 用量",
            "/ai 授权列表 · /ai 群设置 授权编号",
            "/ai 撤销 编号或all",
            "/ai 清空 · /ai 删除密钥 确认",
            "/ai 删除角色 角色编号 确认",
        ], footer="只使用你自己的 AI 账户。QQ 平台及服务器管理员可接触密钥，请使用专用限额 Key；不要给不信任的机器人提供密钥。")
    return help_panel("AI 角色 · 群聊", [
        "每位用户每群可加入一个自己的角色，多人的角色可共存。",
        "① 私聊 /ai 设置，配置自己的密钥、名字和人设",
        "② 私聊 /ai 发布 100，获得一次性接入码",
        "③ 本群 /ai 加入 接入码，再按提示回私聊确认",
        "默认含名字就回复；角色主人可私聊设置该群的触发方式。",
        "",
            "群主/管理员：/ai 暂停 编号 · /ai 恢复 编号",
            "/ai 清空 编号 · /ai 移除 编号",
            "群主/总管理员还可 /ai 移除 all。",
        "角色主人：/ai 群设置 授权编号",
        "角色主人随时可在私聊撤销自己的授权。",
    ], footer="不需要群主批准创建或加入。自动参与群聊需要 QQ 已开放群全消息权限；无需点名并收集 0 会显著增加 Token 消耗。")


def dispatch(ai: AIStore, who: Identity, raw: str) -> str:
    parts = raw.strip().split(maxsplit=1)
    action = ALIASES.get(parts[0].lower(), "") if parts else "status"
    value = parts[1].strip() if len(parts) == 2 else ""
    if action in {"help", "setup"}:
        return help_text(who.private)
    if action == "new":
        role = ai.create_role(who, value)
        return panel("角色已创建并选中", [f"角色编号 {role}",
            "下一步：/ai 服务 glm 或 /ai 服务 qwen beijing",
            "可选：/ai 模型 查看推荐理由并切换",
            "然后：/ai 密钥 你的APIKey",
            "也可 /ai 复用密钥 你已有的角色编号"], footer="以后用 /ai 选择 编号 切换配置对象。创建和选择不会扣调用次数，也不会重置每日额度。")
    if action == "roles":
        rows = ai.roles(who)
        page = number(value or "1", 100)
        start = (page-1)*5
        lines = [f"{'→ ' if r['selected'] else ''}{r['name'] or '未命名'} · {r['id']} · {r['provider']}\n{model_for(r['provider'], r['region'], r['model']).code} · 密钥{'已配置' if r['has_key'] else '未配置'}" for r in rows[start:start+5]]
        return panel(f"我的角色 · 第 {page} 页", lines or ["这一页没有角色；/ai 新建 名字 | 人设"], footer="/ai 选择 编号；/ai 删除角色 编号 确认；/ai 角色列表 页码。只切换配置对象，不影响群内已运行角色。")
    if action == "select":
        ai.select(who, value)
        return panel("已选中角色", "后续服务、密钥、人设、清空和发布操作只针对这个角色。/ai 状态 查看当前配置。")
    if action == "copy-key":
        ai.copy_key(who, value)
        return panel("已复用自己的服务与密钥", "当前角色已使用指定角色相同的服务、地域、模型和 Key。其他角色不变。", footer="各角色独立加密保存；以后修改源角色密钥或模型不会自动同步，请分别更新。")
    if action == "delete-role":
        private_only(who)
        args = value.split()
        if len(args) != 2 or args[1] not in {"确认", "confirm"}:
            raise ToolError("用法：/ai 删除角色 编号 确认。会删除该角色密钥、群授权和聊天记忆，无法从机器人内撤销。")
        ai.delete_role(who, args[0])
        return panel("角色已删除", "只删除该角色及其群授权、密钥和记忆；其他角色不变。今日用量不会重置。")
    if action == "model":
        if not value:
            return _model_panel(ai, who)
        ai.configure(who, "model", value)
        profile = ai.profile(who)
        selected = model_for(profile["provider"], profile["region"], profile["model"])
        recommendation = "这是本服务的默认推荐。" if selected.recommended else "这不是默认推荐；请留意平台价格和免费额度。"
        return panel("AI 模型已切换", [selected.code + " · " + selected.label, selected.reason, recommendation], footer="Key、人设和群授权均保留；当前角色在私聊和各群的旧记忆已清空。模型不会在额度耗尽时自动切换。")
    if action in {"service", "key", "role", "limit"}:
        ai.configure(who, action, value)
        if action == "key":
            return panel("密钥已加密保存", ["不会回显密钥；未发起模型调用。", "用角色名字试聊；/ai 状态 检查名字、人设和服务。", "加入群：/ai 发布 100"], footer="更换密钥影响当前角色已授权的群。建议撤回本条含密钥的 QQ 消息，但撤回不保证清除平台记录。")
        if action == "service":
            profile = ai.profile(who)
            selected = model_for(profile["provider"], profile["region"], profile["model"])
            model_label = "默认推荐" if selected.recommended else "当前模型"
            return panel("AI 服务已选择", [f"{model_label}：{selected.code} · {selected.label}", selected.reason, "可发送 /ai 模型 查看和切换。", "下一步：/ai 密钥 你的APIKey"], footer="更换服务/地域会删除旧密钥并撤销原群授权，以免误扣费。千问地域必须与 Key 一致，与你居住地无关。")
        if action == "role":
            return panel("角色已更新", ["在私聊说一句包含角色名字的话即可试聊。", "进群请发 /ai 发布 100。"], footer="当前角色的名字和人设同步到它加入的所有群，旧记忆已清空；其他角色不变。")
        return panel("每日总额度已更新", "私聊和所有群共用此上限；私聊仍最多 5 次。", footer="改额度不会重置今天已用的次数。")
    if action == "status":
        if who.private:
            profile = ai.profile(who)
            if not profile:
                return help_text(True)
            provider = provider_for(profile["provider"], profile["region"])
            selected = model_for(profile["provider"], profile["region"], profile["model"])
            return panel("AI 角色 · 我的配置", [
                "角色：" + (profile["name"] or "尚未设置"),
                "编号：" + profile["id"],
                provider.label + " · " + selected.code,
                ("默认推荐：" if selected.recommended else "选择理由：") + selected.reason,
                "密钥：" + ("已保存（不展示）" if profile["has_key"] else "未设置"),
                f"总额度：每天 {profile['daily']} 次；私聊最多 5 次",
                "人设：" + (profile["persona"][:180] or "尚未设置"),
                "/ai 模型 查看可选模型 · /ai 发布 100 加群",
                f"删除当前角色：/ai 删除角色 {profile['id']} 确认",
            ], footer="私聊中名字出现在消息的任何位置都会触发，包括误提及。群聊触发方式按每个群的授权单独设置；命令不会发送给 AI。")
        rows = ai.group(who)
        if not rows:
            return help_text(False)
        page = number(value or "1", 1000)
        start = (page-1)*5
        lines = [f"{row['name']} · {'暂停' if row['paused'] else '启用'} · 每日 {row['cap']} 次\n编号 {row['id']} · {_trigger_text(row)}" for row in rows[start:start+5]]
        lines.append(f"共 {len(rows)} 个角色 · 第 {page} 页 · /ai 状态 页码")
        lines.append("/ai help 查看创建、加入和管理步骤")
        return panel("AI 角色 · 本群角色", lines, footer="每个角色使用自己主人的密钥；聊天记忆按角色和群分别保存。")
    if action == "publish":
        cap = number(value) if value else DEFAULT_DAILY
        code = ai.publish(who, cap)
        return panel("把我的角色加入群", [
            f"每个目标群每日最多 {cap} 次，费用由你的 AI 账户承担。",
            "将下面命令发到你要加入的群：", f"/ai 加入 {code}",
            "群里会返回确认码，需由你回本私聊核对后启用。",
        ], footer="接入码 10 分钟有效、只能成功启用一个群；启用后可用 /ai 群设置 授权编号 调整触发方式。无需点名且每条回复会快速消耗 Token。")
    if action == "join":
        result = ai.propose(who, value)
        return panel("等待角色主人确认", [
            f"角色 {result['name']} · 本群标识 {result['group']}",
            "请角色主人回到机器人私聊发送：", f"/ai 确认 {result['code']}",
        ], footer="核对私聊中显示的群标识与此处一致，再启用。接入不授予任何管理权限。")
    if action in {"confirm", "accept"}:
        result = ai.confirm(who, value, apply=action == "accept")
        if action == "confirm":
            return panel("请核对本次群授权", [
                f"目标群标识 {result['group']} · 角色 {result['name']}",
                f"该角色在此群每天最多 {result['cap']} 次，使用你的 Key。",
                "默认只有群成员叫到角色名才回复；之后可单独修改该群设置。",
                "确认与群里标识一致后发送：", f"/ai 启用 {value}",
            ], footer="如果不是你要加入的群，请不要确认。这里的群标识来自 QQ 事件，不是用户填写的群名。")
        return panel("角色已加入该群", [f"{result['name']} · 群标识 {result['group']}",
            f"授权编号 {result['id']} · 本群每日 {result['cap']} 次",
            f"默认提到 {result['name']} 就会回复。",
            f"私聊设置：/ai 群设置 {result['id']}"], footer="可改为无需点名或收集若干条后回复；设置页面会提示 Token 风险。也可随时 /ai 撤销 授权编号。")
    if action == "grants":
        rows = ai.groups(who)
        page = number(value or "1", 1000)
        start = (page-1)*5
        return panel(f"我的群授权 · 第 {page} 页", [f"{r['name']} · {r['id']}\n群 {r['group_tag']} · 每日 {r['cap']} 次 · {'暂停' if r['paused'] else '启用'}\n{_trigger_text(r)}" for r in rows[start:start+5]] or ["这一页没有授权。"], footer="/ai 群设置 授权编号；/ai 撤销 编号；/ai 撤销 all；/ai 授权列表 页码。")
    if action == "group-settings":
        private_only(who)
        args = value.split()
        if not args or len(args) > 4:
            raise ToolError("用法：/ai 群设置 授权编号；或追加 点名 on/off；或 收集 0/最小条数 最大条数。")
        setting = ""
        setting_value = ""
        if len(args) >= 2:
            setting = {"点名": "mention", "mention": "mention", "收集": "collect", "collect": "collect"}.get(args[1].lower(), "")
            if not setting:
                raise ToolError("可设置：点名 on/off，或收集 0/最小条数 最大条数。")
            setting_value = " ".join(args[2:])
        row = ai.group_settings(who, args[0], setting, setting_value)
        return _group_settings_panel(row)
    if action == "revoke":
        ai.revoke(who, value)
        return panel("授权已撤销", "对应角色在群内停用，相关聊天记忆已清空；不会影响其他人的角色。")
    if action == "delete-key":
        private_only(who)
        if value not in {"确认", "confirm"}:
            return panel("删除当前角色的密钥", "请发送 /ai 删除密钥 确认。会撤销当前角色的所有群授权并清空它的聊天记忆；其他角色不变。")
        ai.delete_key(who)
        return panel("密钥已删除", "当前角色已在所有群停用。建议去 AI 平台撤销此 Key（会影响复用此 Key 的其他角色）；备份或 QQ 历史消息可能保留旧副本。")
    if action == "usage":
        result = ai.usage(who)
        return panel("AI 今日用量", [f"私聊 {result['private']}/5 次 · 合计 {result['total']} 次调用",
            f"已报告输入 {result['input']} / 输出 {result['output']} tokens",
            f"未取得用量的调用 {result['unknown']} 次"], footer="北京时间每日 00:00 重置次数。按发起请求计数，失败不退次数、不自动重试；真实账单以 AI 平台为准。")
    if action == "clear" and who.private:
        ai.clear(who)
        return panel("私聊记忆已清空", "仅清空当前选中角色的私聊记忆，不影响其他角色和各群记忆。")
    if action in {"pause", "resume", "clear", "remove"}:
        ai.moderate(who, action, value)
        return panel("本群角色已更新", "操作已完成，相关角色的旧记忆已清空；不会影响其他群。")
    if action == "global":
        if value not in {"on", "off"}:
            raise ToolError("总管理员私聊：/ai 全局 on 或 /ai 全局 off。只影响聊天调用，不锁住删除密钥入口。")
        ai.global_switch(who, value == "on")
        return panel("AI 全局开关已更新", "已启用聊天。" if value == "on" else "已关闭所有 AI 聊天，配置和撤销入口仍可用。")
    raise ToolError("没有这个 AI 操作。发送 /ai 设置 查看步骤；不要将密钥作为普通聊天消息发送。")
