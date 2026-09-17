from __future__ import annotations

import asyncio

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import (
    GroupMessageCreateEvent,
    MessageCreateEvent, MessageEvent,
)
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor, run_preprocessor
from nonebot.params import Command, CommandArg
from nonebot.typing import T_State

from bot_tools import community, games, media, gallery, group_status
from bot_tools.catalog import command_directory
from bot_tools.group_names import refresh as refresh_group_name
from bot_tools.plugin_runtime import bounded, command_eligible, get_store, identity
from bot_tools.storage import ToolError
from message_ui import error_panel, help_panel, panel, public_error_message


COMMANDS = {"fsx","ofish","hunt","custom_reply","vote","lottery","cat","waifu","gif","image","tex","duilian","akhr","group","ginfo","command","left_reply","bot","comment"}


def _visible_tool_error(error: ToolError) -> str:
    detail = str(error).strip()
    visible = public_error_message(
        error,
        "功能暂时不可用，请稍后重试或联系管理员。",
    )
    if visible != detail:
        logger.error("Suppressed internal toolbox error: {}", detail)
    return visible


toolbox = on_command("toolbox",aliases=COMMANDS,force_whitespace=True,rule=command_eligible,priority=10,block=True)
# Intentionally no to_me(): GROUP_MESSAGE_CREATE is delivered only with QQ's
# full-message permission; GROUP_AT_MESSAGE_CREATE is a subclass and also works.
keywords = on_message(priority=30,block=False)
MEDIA_SLOTS = asyncio.Semaphore(2)
MAX_GALLERY_UPLOADS = 10
GROUP_NAME_TASKS: set[asyncio.Task] = set()


@event_preprocessor
async def remember_group_name(bot: Bot, event: MessageEvent):
    """Refresh display metadata once per day without blocking normal commands."""
    if not isinstance(event, GroupMessageCreateEvent):
        return
    who = identity(bot, event)
    await asyncio.to_thread(get_store().remember_scope, who)
    # Test/fallback bot objects are not connected and must never make API calls.
    if not getattr(bot, "ready", False):
        return
    extra = getattr(event, "model_extra", None) or {}
    task = asyncio.create_task(refresh_group_name(
        get_store(), bot, event.group_openid, who.scope_key,
        str(extra.get("group_name") or ""),
    ))
    GROUP_NAME_TASKS.add(task)
    task.add_done_callback(GROUP_NAME_TASKS.discard)


def is_help(name: str, raw: str) -> bool:
    # /ofish without arguments actually computes the next departures.
    return raw == "help" or (not raw and name not in {"ofish","cat","waifu"})


@run_preprocessor
async def enforce_switches(bot: Bot, event: MessageEvent, matcher: Matcher, state: T_State):
    if isinstance(matcher, keywords):
        return
    prefix = state.get("_prefix",{})
    command = prefix.get("command",())
    if not command:
        return
    if command[0] == "ai":
        # Disable name-triggered AI chat, never lock users out of deleting keys.
        return
    try:
        await asyncio.to_thread(community.gate,get_store(),identity(bot,event),command[0])
    except ToolError as exc:
        await bot.send(event,bounded(error_panel(_visible_tool_error(exc))))
        raise IgnoredException("Command disabled for this scope")


def toolbox_help() -> str:
    return command_directory()


async def dispatch(bot: Bot, event: MessageEvent, name: str, raw: str) -> str | MessageSegment:
    store, who = get_store(), identity(bot,event)
    # Ordinary commands and group administration never issue identity codes.
    # Only a successful private one-time invitation creates a binding record.
    if name == "toolbox":
        return toolbox_help()
    if name == "ginfo":
        return await asyncio.to_thread(group_status.overview, store, who, raw)
    if name == "group" and (raw == "status" or raw.startswith("status ")):
        return await asyncio.to_thread(group_status.overview, store, who, raw[6:].strip())
    if raw == "help" and name in {"bot","group","command","left_reply","custom_reply","hunt"}:
        raw = ""
    # Apply cooldown to all non-help tool requests, including administration.
    if not is_help(name,raw):
        await asyncio.to_thread(store.throttle,who.actor)
        await asyncio.to_thread(community.gate,store,who,name,True)
    if name in {"bot","group","command","left_reply","comment"}:
        return await asyncio.to_thread(community.management,store,who,name,raw)
    if name in {"vote","lottery"}:
        return await asyncio.to_thread(community.activities,store,who,name,raw)
    if name in {"custom_reply","hunt"}:
        return await asyncio.to_thread(getattr(community,name),store,who,raw)
    if name in {"fsx","ofish","akhr"}:
        return await asyncio.to_thread(getattr(games,name),raw)
    if name == "duilian":
        return media.duilian(raw)
    if name in {"tex","gif"}:
        if not raw or raw == "help":
            return help_panel("公式图片" if name=="tex" else "动态文字", [r"/tex \frac{a}{b}=\sqrt{x}" if name=="tex" else "/gif 今天也要开心", "本地生成，不将文字发送到第三方。", "Mathtext 数学子集，不支持完整 LaTeX。" if name=="tex" else "最多 36 字；若客户端不播放动图，请打开原图。"])
        async with MEDIA_SLOTS:
            result = await asyncio.to_thread(media.render_tex if name=="tex" else media.render_gif,raw)
        return MessageSegment.file_image(result,"formula.png" if name=="tex" else "text.gif")
    if name == "image":
        args = raw.split()
        if args and args[0] == "add":
            if len(args) != 2:
                raise ToolError("用法：/image add 分类，并在同一条消息中上传 1～10 张图片。")
            category = media.gallery_category(args[1])
            attachments = getattr(event,"attachments",None) or []
            if not attachments:
                raise ToolError("请在同一条消息中附上 1～10 张图片。")
            if len(attachments) > MAX_GALLERY_UPLOADS:
                raise ToolError(f"一次最多上传 {MAX_GALLERY_UPLOADS} 张图片，请分批发送。")
            if any(not getattr(attachment,"url",None)
                   or not str(getattr(attachment,"content_type","") or "").startswith("image/")
                   for attachment in attachments):
                raise ToolError("附件中只能包含直接上传的 QQ 图片，不要发送文件或网页链接。")
            target = await asyncio.to_thread(gallery.current, store, who)
            if len(attachments) == 1:
                async with MEDIA_SLOTS:
                    image = await media.fetch_image(attachments[0].url)
                    item_id = await asyncio.to_thread(media.gallery_add,store,who,category,image,target)
                note = "GIF 原文件完整保留。" if media.image_filename(image).endswith(".gif") else "静态图片已去除元数据并压缩。"
                return panel("图片已入库", [f"分类 {category} · 编号 {item_id}", "保存到：" + target.label],
                             footer=note + ("使用公共图库的群均可取图。" if target.public else "仅当前会话可用。"))
            results, succeeded, gif_count = [], 0, 0
            async with MEDIA_SLOTS:
                for index, attachment in enumerate(attachments, 1):
                    try:
                        image = await media.fetch_image(attachment.url)
                        item_id = await asyncio.to_thread(
                            media.gallery_add,store,who,category,image,target)
                    except ToolError as exc:
                        results.append(f"第 {index} 张：失败 · {_visible_tool_error(exc)}")
                        continue
                    succeeded += 1
                    gif_count += media.image_filename(image).endswith(".gif")
                    results.append(f"第 {index} 张：已入库 · 编号 {item_id}")
            notes = [f"分类 {category} · 成功 {succeeded}/{len(attachments)} 张", "保存到：" + target.label]
            if gif_count:
                notes.append(f"其中 {gif_count} 张 GIF 保留原文件。")
            return panel("批量图片上传完成", notes + results,
                         footer="失败图片不会影响其他图片。" +
                         ("使用公共图库的群均可取图。" if target.public else "仅当前会话可用。"))
        if len(args)!=1 or args[0] in {"help","list","del","add","status"}:
            return await asyncio.to_thread(media.gallery_command,store,who,raw)
        category = args[0]
    else:
        if raw == "help":
            return await asyncio.to_thread(media.gallery_command,store,who,"")
        if raw:
            raise ToolError(f"/{name} 不需要参数。")
        category = name
    target = await asyncio.to_thread(gallery.current, store, who)
    image = await asyncio.to_thread(media.gallery_get,store,who,category,target)
    if image is None and name == "cat":
        async with MEDIA_SLOTS:
            image = await media.cat_picture()
            image = await asyncio.to_thread(media.checked_image,image)
    if image is None:
        raise ToolError(f"{target.label}的 {category} 分类为空。所有人都可发送 /image add {category} 并附带合规图片。")
    return MessageSegment.file_image(image,media.image_filename(image))


@toolbox.handle()
async def handle_toolbox(bot: Bot, event: MessageEvent, matcher: Matcher,
                         command: tuple[str,...] = Command(), args: Message = CommandArg()):
    name = command[0]
    raw = args.extract_plain_text().strip()
    if len(raw)>800:
        await matcher.finish(error_panel("输入过长，请控制在 800 字以内。"))
    try:
        reply = await dispatch(bot,event,name,raw)
    except ToolError as exc:
        reply = error_panel(_visible_tool_error(exc))
    except Exception as exc:
        # Never print input, feedback, tokens, raw OpenIDs, URLs or SQL values.
        logger.error("Toolbox command {} failed ({})",name,type(exc).__name__)
        reply = error_panel("工具暂时未完成，请稍后重试或提交 /comment 反馈。")
    if isinstance(reply,str):
        reply = bounded(reply)
    try:
        await matcher.send(reply)
    except Exception as exc:
        logger.warning("Toolbox reply delivery failed ({})",type(exc).__name__)
        if not isinstance(reply,str):
            await matcher.send(error_panel("图片已生成，但 QQ 未接受发送。请检查机器人图片消息权限后重试。"))


def keyword_eligible(event: MessageEvent) -> bool:
    if getattr(getattr(event,"author",None),"bot",False):
        return False
    return event.is_tome() or isinstance(event,(GroupMessageCreateEvent,MessageCreateEvent))


@keywords.handle()
async def handle_keyword(bot: Bot,event: MessageEvent):
    if not keyword_eligible(event):
        return
    raw = event.get_plaintext().strip()
    if not raw or len(raw)>30 or raw.startswith("/"):
        return
    store,who = get_store(),identity(bot,event)
    reply = await asyncio.to_thread(community.keyword_lookup,store,who,raw)
    if reply is None:
        return
    try:
        # Cooldown is per group as well as per user, to avoid reply storms.
        await asyncio.to_thread(store.throttle,"keyword:"+who.scope_key,3)
        await asyncio.to_thread(store.throttle,who.actor,2)
        await asyncio.to_thread(community.gate,store,who,"custom_reply",True)
    except ToolError:
        return
    # A matched keyword is a conversation reply, not a configuration card.
    await keywords.send(bounded(reply))


@get_driver().on_startup
async def startup_toolbox():
    await asyncio.to_thread(get_store)
    logger.info("Local toolbox ready: persistent state, scoped permissions, private feedback inbox")
