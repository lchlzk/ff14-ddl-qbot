"""Opt-in, bring-your-own-key roleplay for QQ C2C and group messages."""
from __future__ import annotations

import asyncio
from functools import lru_cache

from nonebot import on_command, on_message
from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from bot_tools import ai_commands, ai_provider, community
from bot_tools.ai_store import AIStore
from bot_tools.ai_queue import FairLimiter
from bot_tools.security import active_ai_key
from bot_tools.storage import Store, ToolError
from message_ui import error_panel, public_error_message
from bot_tools.plugin_runtime import bounded, identity


def eligible(event: MessageEvent) -> bool:
    return (isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent))
            and not getattr(event.author, "bot", False))


def chat_eligible(event: MessageEvent) -> bool:
    return eligible(event) and not AIStore._configuration_text(event.get_plaintext())


command = on_command("ai", force_whitespace=True, rule=eligible, priority=5, block=True)
chat = on_message(rule=chat_eligible, priority=25, block=False)
SLOTS = FairLimiter()


@lru_cache(maxsize=1)
def get_ai() -> AIStore:
    return AIStore(Store())


def _visible_ai_error(error: ToolError, fallback: str) -> str:
    detail = str(error).strip()
    visible = public_error_message(error, fallback)
    if visible != detail:
        logger.error("Suppressed internal AI error: {}", detail)
    return visible


@command.handle()
async def configure(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    try:
        ai = get_ai()
        who = identity(bot, event)
        await asyncio.to_thread(ai.store.throttle, "ai-config:" + who.actor, 1)
        result = await asyncio.to_thread(ai_commands.dispatch, ai, who, args.extract_plain_text())
    except ToolError as exc:
        result = error_panel(_visible_ai_error(exc, "AI 配置暂时不可用，请稍后重试或联系管理员。"))
    except Exception as exc:
        # Do not include exception text, traceback, state or message arguments.
        logger.warning("AI configuration failed ({})", type(exc).__name__)
        result = error_panel("AI 配置暂时不可用，请稍后重试或联系管理员。")
    await bot.send(event, MessageSegment.text(bounded(result)))


async def reply_one(bot: Bot, event: MessageEvent, ai: AIStore, owner: str,
                    text: str, *, batched: bool = False):
    request = None
    token = None
    try:
        who = identity(bot, event)
        async with SLOTS.slot(who.scope_key):
            # /command disable ai stops group chat calls, not private key
            # deletion or moderation commands. Configuration always remains usable.
            await asyncio.to_thread(community.gate, ai.store, who, "ai")
            request = await asyncio.to_thread(
                ai.prepare, who, text, str(event.id), owner, batched=batched
            )
            if request is None:
                return
            token = active_ai_key.set(request.key)
            result = await ai_provider.complete(
                request.provider, request.region, request.key, request.messages,
                model=request.model,
            )
            if not await asyncio.to_thread(ai.finish, request, result):
                return  # Revoked/paused/reconfigured while the provider was running.
            await bot.send(event, MessageSegment.text(f"{request.name}：{result.text}"))
    except ToolError as exc:
        label = request.name + " · " if request else ""
        message = _visible_ai_error(exc, "AI 服务暂时不可用，请稍后重试或联系管理员。")
        await bot.send(event, MessageSegment.text(bounded(error_panel(label + message))))
    except Exception as exc:
        logger.warning("AI chat failed ({})", type(exc).__name__)
        await bot.send(event, MessageSegment.text(error_panel("AI 暂时无法回复，请稍后再试。")))
    finally:
        if request is not None:
            # A failed/aborted request remains counted; duplicate deliveries
            # must not retry paid calls. Completed runs are left unchanged.
            try:
                await asyncio.to_thread(ai.finish, request)
            except Exception as exc:
                logger.warning("AI usage finalization failed ({})", type(exc).__name__)
        if token is not None:
            active_ai_key.reset(token)


@chat.handle()
async def respond(bot: Bot, event: MessageEvent, matcher: Matcher):
    text = event.get_plaintext().strip()
    if not text or text.startswith("/"):
        return
    try:
        ai = get_ai()
        targets = await asyncio.to_thread(
            ai.collect_targets, identity(bot, event), text, str(event.id)
        )
    except Exception as exc:
        logger.warning("AI target lookup failed ({})", type(exc).__name__)
        return
    if not targets:
        return
    matcher.stop_propagation()
    await asyncio.gather(*(
        reply_one(bot, event, ai, target.owner, target.prompt, batched=target.batched)
        for target in targets
    ))
