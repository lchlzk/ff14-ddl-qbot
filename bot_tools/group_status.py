"""Read-only, privacy-safe overview of one QQ group's local configuration."""
from __future__ import annotations

from message_ui import panel
from . import gallery, group_extensions
from .ai_store import AIStore
from .learning_chat import LearningStore
from .community import MANAGED
from .storage import Identity, Store, ToolError


PAGE_SIZE = 5


def _number(value: str) -> int:
    if not value:
        return 1
    try:
        page = int(value)
    except ValueError:
        raise ToolError("用法：/ginfo [页码]，或 /group status [页码]") from None
    if not 1 <= page <= 1000:
        raise ToolError("页码应为 1～1000。")
    return page


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def overview(store: Store, who: Identity, raw: str = "") -> str:
    if who.private or not who.scope.startswith("group:"):
        raise ToolError("群设置总览只能在目标 QQ 群内查看。")
    store.require_admin(who)
    page = _number(raw.strip())

    # Copy only the fields needed for display, then release the document lock
    # before opening the gallery and AI read transactions.
    with store.state(who.scope_key) as doc:
        plugin_lines = group_extensions.overview(doc)
        quota = doc.get("quota", 100)
        replies = doc.get("replies") if isinstance(doc.get("replies"), dict) else {}
        disabled = doc.get("disabled") if isinstance(doc.get("disabled"), list) else []
        disabled = sorted({item for item in disabled if isinstance(item, str) and item in MANAGED})

    target, image_count, image_bytes = gallery.stats(store, who)
    ai = AIStore(store)
    learning = LearningStore(store)
    roles = ai.group(who)
    learned = learning.settings(who)
    start = (page - 1) * PAGE_SIZE
    selected = roles[start:start + PAGE_SIZE]
    gallery_limit = "不设张数上限" if target.public else f"{image_count}/100 张"
    local_ai = "本群关闭" if "ai" in disabled else "本群启用"
    lines = [
        f"群标识：{who.scope_key[:10]}",
        *plugin_lines,
        f"工具箱额度：每人每天 {quota} 次",
        "  设置：/group quota 100",
        f"图库：{'公共图库' if target.public else '本群图库'} · {gallery_limit} · {_size(image_bytes)}",
        "  切换：/group gallery public 或 local（仅群主/总管理员）",
        "  图片：/image add 分类 并附图 · /image del 编号",
        "本群已关闭功能：" + ("、".join("/" + item for item in disabled) if disabled else "无"),
        "  查看：/command list",
        "  示例：/command disable cat（关闭）· /command enable cat（恢复）",
        f"关键词：{len(replies)} 条",
        "  关键词：/custom_reply set 关键词 | 内容",
        f"群聊学习：{'开启' if learned['enabled'] else '关闭'} · "
        f"{'公开库' if learned.get('library_mode', 'public') == 'public' else '本群私有库'} · "
        f"{learned['pairs']} 组回复 · 回复阈值 {learned['answer_threshold']}",
        "  设置与隐私说明：/learn",
        f"AI 聊天：{'全局启用' if ai.globally_enabled(who.bot) else '全局关闭'} · {local_ai} · 本群 {len(roles)} 个角色",
        "  本群开关：/command disable ai · /command enable ai",
        "  全局开关：总管理员私聊 /ai 全局 off 或 on",
        "AI 角色：",
    ]
    for row in selected:
        lines.append(f"{row['name']}\n角色编号 {row['id']}")
    if not selected:
        lines.append("本页没有 AI 角色。")
    lines.append(f"角色第 {page} 页 · 每页 {PAGE_SIZE} 个")
    return panel(
        "本群设置总览",
        lines,
        footer=("角色管理：/ai 暂停 编号 · /ai 恢复 编号 · /ai 清空 编号 · "
                "/ai 移除 编号。加入角色：角色主人私聊 /ai 设置。"),
    )
