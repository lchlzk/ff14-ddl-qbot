"""nonebot-adapter-qq entry point for the AGPL learning-chat port."""
from __future__ import annotations

import asyncio
from functools import lru_cache

from nonebot import on_command, on_message
from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg

from bot_tools import community, learning_commands, media
from bot_tools.learning_chat import LearningContent, LearningReply, LearningStore
from bot_tools.storage import Store, ToolError
from message_ui import error_panel, public_error_message
from bot_tools.plugin_runtime import bounded, identity


MEDIA_SLOTS = asyncio.Semaphore(2)


def group_event(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageCreateEvent) and not getattr(event.author, "bot", False)


def learnable_group_event(event: MessageEvent) -> bool:
    """Keep slash commands out before command preprocessors are invoked."""
    return group_event(event) and not event.get_plaintext().strip().startswith("/")


command = on_command(
    "learn", aliases={"learning", "学习"}, force_whitespace=True,
    rule=group_event, priority=6, block=True,
)
# Observe before the optional AI/keyword responders stop propagation. Commands
# are explicitly excluded below and are handled by the matcher above.
chat = on_message(rule=learnable_group_event, priority=20, block=False)


@lru_cache(maxsize=1)
def get_learning() -> LearningStore:
    return LearningStore(Store())


def _reply_user(event: GroupMessageCreateEvent) -> str:
    author = getattr(getattr(event, "reply", None), "author", None)
    if not author or getattr(author, "bot", False):
        return ""
    return str(
        getattr(author, "member_openid", None)
        or getattr(author, "user_openid", None)
        or getattr(author, "id", None)
        or ""
    )


@command.handle()
async def configure(bot: Bot, event: GroupMessageCreateEvent, args: Message = CommandArg()):
    try:
        learning = get_learning()
        who = identity(bot, event)
        await asyncio.to_thread(learning.store.throttle, "learning-config:" + who.actor, 1)
        reply_text = str(getattr(getattr(event, "reply", None), "content", "") or "")
        result = await asyncio.to_thread(
            learning_commands.dispatch,
            learning, learning.store, who, args.extract_plain_text(),
            reply_text=reply_text, reply_user=_reply_user(event),
        )
    except ToolError as exc:
        detail = str(exc).strip()
        visible = public_error_message(exc, "群聊学习暂时不可用，请稍后重试或联系管理员。")
        if visible != detail:
            logger.error("Suppressed internal learning-chat error: {}", detail)
        result = error_panel(visible)
    except Exception as exc:
        logger.warning("Learning-chat configuration failed ({})", type(exc).__name__)
        result = error_panel("群聊学习暂时不可用，请稍后重试或联系管理员。")
    await bot.send(event, MessageSegment.text(bounded(result)))


async def _message_content(event: GroupMessageCreateEvent, *, allow_image: bool) -> LearningContent | None:
    text = event.get_plaintext().strip()
    if text:
        return LearningContent.from_text(text)
    if not allow_image:
        return None
    attachments = getattr(event, "attachments", None) or []
    if len(attachments) != 1:
        return None
    attachment = attachments[0]
    if not attachment.url or not attachment.content_type.startswith("image/"):
        return None
    async with MEDIA_SLOTS:
        image = await media.fetch_image(attachment.url)
        image = await asyncio.to_thread(media.checked_image, image)
    return LearningContent.from_image(image)


def _referenced_content(event: GroupMessageCreateEvent) -> LearningContent | None:
    reply = getattr(event, "reply", None)
    if not reply:
        return None
    return LearningContent.from_text(str(getattr(reply, "content", "") or ""))


async def _send_reply(bot: Bot, event: GroupMessageCreateEvent, reply: LearningReply) -> None:
    if reply.kind == "image" and reply.image:
        await bot.send(event, MessageSegment.file_image(reply.image, media.image_filename(reply.image)))
    elif reply.text:
        await bot.send(event, MessageSegment.text(bounded(reply.text)))


@chat.handle()
async def learn_from_group(bot: Bot, event: GroupMessageCreateEvent):
    learning = get_learning()
    who = identity(bot, event)
    try:
        await asyncio.to_thread(community.gate, learning.store, who, "learn")
        settings = await asyncio.to_thread(learning.config, who)
        if not settings["enabled"]:
            return
        content = await _message_content(event, allow_image=bool(settings["image_enabled"]))
        if content is None:
            return
        reply = await asyncio.to_thread(
            learning.observe, who, content, str(event.id),
            addressed=event.is_tome(), referenced=_referenced_content(event),
        )
        if reply is None:
            return
        # A group-level cooldown limits collisions between learned replies,
        # repeats and other rapid event deliveries. Learning itself is kept.
        await asyncio.to_thread(learning.store.throttle, "learning-reply:" + who.scope_key, 3)
        await _send_reply(bot, event, reply)
    except ToolError:
        return
    except Exception as exc:
        # Passive learning failures stay silent and never expose group text,
        # attachment URLs, identifiers or database values in logs.
        logger.warning("Learning-chat passive handler failed ({})", type(exc).__name__)
