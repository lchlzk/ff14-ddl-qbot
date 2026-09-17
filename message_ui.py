from __future__ import annotations

from collections.abc import Iterable


DIVIDER = "────────────"


# Low-level failures are useful to operators, but implementation details do not
# help an ordinary QQ or board user recover. Command entry points pass expected
# errors through this filter before rendering them.
_INTERNAL_ERROR_MARKERS = (
    "数据目录",
    "媒体目录",
    "存储路径",
    "存储标识",
    "服务器磁盘",
    "原文件缺失",
    "cos_",
    "腾讯云 cos",
    "cos 中",
    "media_object_backend",
    "容器内的普通密钥文件",
    "python sdk",
    "主密钥",
    "加密文件",
    "无法解密",
    "服务器网络",
    "服务器配置",
    "鉴权参数",
    "dns",
    "tls 证书",
    "服务器未安装",
    "浏览器降级组件",
    "服务器配置的",
    "trickcal_web_public_url",
    "公开蜡笔板网页必须使用 https",
    "bot.cmd ",
    "./bot.sh ",
    "client id",
    "client secret",
    "fflogs_",
    "otter_api_",
)


def public_error_message(error: BaseException | str, fallback: str) -> str:
    """Return a user-safe error without exposing deployment internals."""
    detail = str(error).strip()
    if not detail:
        return fallback.strip()
    normalized = detail.casefold()
    if any(marker.casefold() in normalized for marker in _INTERNAL_ERROR_MARKERS):
        return fallback.strip()
    return detail


def panel(
    title: str,
    lines: Iterable[str] | str = (),
    *,
    icon: str = "✨",
    subtitle: str = "",
    footer: str = "",
) -> str:
    """Build a compact QQ-friendly plain-text card.

    QQ official bots cannot rely on Markdown rendering in every chat surface, so
    the built-in replies share a small visual language made only from plain text.
    """
    body = [lines] if isinstance(lines, str) else list(lines)
    cleaned: list[str] = []
    for line in body:
        raw_value = str(line).rstrip()
        value = raw_value if raw_value.strip() else ""
        if value or (cleaned and cleaned[-1]):
            cleaned.append(value)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    result = [f"{icon} {title.strip()}"]
    if subtitle.strip():
        result.append(subtitle.strip())
    if cleaned:
        result.extend(("", DIVIDER, *cleaned))
    if footer.strip():
        result.extend(("", f"ℹ️ {footer.strip()}"))
    return "\n".join(result)


def help_panel(title: str, lines: Iterable[str] | str, *, footer: str = "") -> str:
    return panel(title, lines, icon="🧭", footer=footer)


def error_panel(message: str, *, hint: str = "") -> str:
    lines = [message.strip()]
    if hint.strip():
        lines.append(f"💡 {hint.strip()}")
    return panel("没有完成", lines, icon="⚠️")


def cooldown_panel(seconds: float) -> str:
    return panel(
        "操作太快啦",
        f"请等待 {max(seconds, 0.1):.1f} 秒后再试。",
        icon="⏳",
    )
