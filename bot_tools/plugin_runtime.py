"""Shared runtime helpers for independently installed first-party plugins.

Keep these helpers in the bot core so a plugin never needs to import another
plugin merely to resolve the current QQ identity or render a bounded reply.
"""

from __future__ import annotations

from functools import lru_cache

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    MessageEvent,
)

from .storage import Identity, Store


def command_eligible(event: MessageEvent) -> bool:
    if getattr(getattr(event, "author", None), "bot", False):
        return False
    # Full-message QQ group events do not require an explicit @mention.
    return event.is_tome() or isinstance(
        event,
        (GroupMessageCreateEvent, C2CMessageCreateEvent, DirectMessageCreateEvent),
    )


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store()


def identity(bot: Bot, event: MessageEvent) -> Identity:
    private = isinstance(event, (C2CMessageCreateEvent, DirectMessageCreateEvent))
    user = event.get_user_id()
    group_role = None
    if isinstance(event, GroupMessageCreateEvent):
        scope = "group:" + event.group_openid
        if not getattr(event.author, "bot", False):
            role = getattr(event.author, "member_role", None)
            if role in {"member", "admin", "owner"}:
                group_role = role
    elif private:
        scope = "private:" + user
    else:
        scope = "channel:" + str(getattr(event, "channel_id", event.get_session_id()))
    return Identity(str(bot.self_id), scope, user, private, group_role)


def bounded(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= 1800:
        return text
    suffix = "\n…内容较多，请缩小查询范围。"
    return encoded[: 1800 - len(suffix.encode())].decode("utf-8", "ignore") + suffix
