"""Small, allowlisted text-only clients. Never log request/response bodies."""
from __future__ import annotations

import asyncio
import codecs
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from .storage import ToolError
from . import http_clients


@dataclass(frozen=True)
class Provider:
    label: str
    model: str
    url: str


@dataclass(frozen=True)
class ModelOption:
    code: str
    label: str
    reason: str
    recommended: bool = False


# Regional keys must match their endpoint. No user-supplied URL or paid fallback.
PROVIDERS = {
    ("glm", "cn"): Provider("智谱 GLM · 国内", "glm-4.7-flash",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
    ("qwen", "beijing"): Provider("千问 Character · 北京", "qwen-flash-character-2026-02-26",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
    ("qwen", "singapore"): Provider("千问 Character · 新加坡", "qwen-flash-character",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
    ("qwen", "virginia"): Provider("千问 Character · 弗吉尼亚", "qwen-flash-character-2026-02-26",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions"),
}

# Only text-chat models verified against each provider's compatible endpoint are
# selectable.  Keeping this list explicit prevents typos from silently selecting
# an unexpected paid or specialist model.
MODELS = {
    ("glm", "cn"): (
        ModelOption("glm-4.7-flash", "免费 Flash", "免费、速度快，日常角色聊天性价比最高。", True),
        ModelOption("glm-4.7-flashx", "低价 FlashX", "比免费版更稳的低价档，适合调用量较大的角色。"),
        ModelOption("glm-4.7", "通用旗舰", "指令遵循更强，但通常比 Flash 系列更贵。"),
        ModelOption("glm-5", "新一代通用", "复杂对话能力更强，费用和响应时间通常更高。"),
        ModelOption("glm-5-turbo", "新一代 Turbo", "在新一代能力与响应速度之间取平衡。"),
        ModelOption("glm-5.2", "最新旗舰", "能力档位最高，适合效果优先，不建议只为省额度切换。"),
    ),
    ("qwen", "beijing"): (
        ModelOption(
            "qwen-flash-character-2026-02-26", "Character 固定版",
            "专为角色扮演优化、版本固定且成本较低，角色表现更稳定。", True,
        ),
        ModelOption("qwen-flash-character", "Character 动态版", "专为角色扮演优化，会随平台更新能力。"),
        ModelOption("qwen-plus-character", "Character 增强版", "专为角色扮演优化，效果更强但通常更贵。"),
        ModelOption("qwen3.7-flash", "通用 Flash", "速度和成本优先，但不是角色扮演专用模型。"),
        ModelOption("qwen3.7-plus", "通用 Plus", "通用对话能力更强，但角色还原和成本未必优于 Character。"),
    ),
    ("qwen", "singapore"): (
        ModelOption("qwen-flash-character", "Character 动态版", "国际部署的角色扮演模型，速度和成本优先。", True),
        ModelOption("qwen-plus-character", "Character 增强版", "国际部署的增强角色模型，通常费用更高。"),
    ),
    ("qwen", "virginia"): (
        ModelOption(
            "qwen-flash-character-2026-02-26", "Character 固定版",
            "全球部署的固定角色模型，角色表现稳定且成本较低。", True,
        ),
        ModelOption("qwen-flash-character", "Character 动态版", "全球部署的动态角色扮演模型。"),
        ModelOption("qwen-plus-character", "Character 增强版", "全球部署的增强角色模型，通常费用更高。"),
    ),
}
MAX_REPLY_TOKENS = 256
MAX_REPLY_CHARS = 400
MAX_RESPONSE_BYTES = 128 * 1024
GLM_READ_TIMEOUT = 90
GLM_TOTAL_TIMEOUT = 120
NO_RETRY = "为避免重复扣费，没有自动重试。"
GLM_LIMIT_ERRORS = {
    "1113": "智谱账户余额不足，请密钥主人检查账户余额。",
    "1302": "智谱请求过于频繁或并发已满，请减少同时调用。",
    "1304": "智谱今日调用次数已达上限，请等待平台额度重置。",
    "1305": "智谱模型当前访问量过大，服务繁忙，请稍后再试。",
    "1308": "智谱使用额度已达上限，请在智谱控制台查看重置时间。",
    "1310": "智谱每周或每月使用额度已达上限，请在智谱控制台查看重置时间。",
}


def provider_for(provider: str, region: str) -> Provider:
    result = PROVIDERS.get((provider, region))
    if result is None:
        raise ToolError("服务或地域不支持。可用：glm；qwen beijing / singapore / virginia。")
    return result


def model_options(provider: str, region: str) -> tuple[ModelOption, ...]:
    provider_for(provider, region)
    return MODELS[(provider, region)]


def model_for(provider: str, region: str, model: str = "") -> ModelOption:
    options = model_options(provider, region)
    requested = model.strip().lower()
    if requested in {"", "default", "recommended", "推荐", "默认"}:
        return next(option for option in options if option.recommended)
    for option in options:
        if option.code == requested:
            return option
    raise ToolError("当前服务或地域不支持这个模型。请发送 /ai 模型 查看可选列表。")


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    usage_known: bool = False


async def _bounded_bytes(response: httpx.Response) -> AsyncIterator[bytes]:
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > MAX_RESPONSE_BYTES:
            raise ToolError("AI 返回内容过大，已停止接收。")
        yield chunk


async def _sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    # Preserve split UTF-8 characters, a split BOM, and CR/LF/CRLF boundaries.
    # Bound bytes before decoding, including comments and unterminated lines.
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder("utf-8-sig")(errors="strict"), translate=True)
    pending = ""
    async for chunk in _bounded_bytes(response):
        pending += decoder.decode(chunk)
        while "\n" in pending:
            line, _, pending = pending.partition("\n")
            yield line
    pending += decoder.decode(b"", final=True)
    while "\n" in pending:
        line, _, pending = pending.partition("\n")
        yield line
    # SSE dispatch requires an empty line; an unfinished event is not success.


async def _stream_result(response: httpx.Response) -> tuple[str, object]:
    parts: list[str] = []
    data_lines: list[str] = []
    event_type = ""
    usage: object = None
    async for line in _sse_lines(response):
        if line:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_type = value
            continue
        if event_type == "error":
            raise ToolError("AI 服务在生成回复时返回错误；" + NO_RETRY)
        event_type = ""
        if not data_lines:
            continue
        raw = "\n".join(data_lines)
        data_lines.clear()
        if raw.strip() == "[DONE]":
            return "".join(parts), usage
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid event")
        if "error" in data:
            raise ToolError("AI 服务在生成回复时返回错误；" + NO_RETRY)
        if data.get("usage") is not None:
            usage = data["usage"]
        choices = data.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("invalid choices")
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError("invalid choice")
            if choice.get("index", 0) != 0:
                continue
            if choice.get("finish_reason") not in (None, "stop", "length"):
                raise ToolError("AI 未能完成可显示的回复；" + NO_RETRY)
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ValueError("invalid delta")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ValueError("invalid content")
                # Never collect reasoning_content, tool calls, or metadata.
                parts.append(content)
    raise ToolError("AI 回复传输中断，未收到完整结束标记；" + NO_RETRY)


async def _json_result(response: httpx.Response) -> tuple[str, object]:
    body = bytearray()
    async for chunk in _bounded_bytes(response):
        body.extend(chunk)
    data = json.loads(body)
    if not isinstance(data, dict) or "error" in data:
        raise ValueError("invalid response")
    return data["choices"][0]["message"]["content"], data.get("usage")


async def _glm_limit_error(response: httpx.Response) -> str | None:
    # HTTP 429 can mean either account limits or platform overload. Read only
    # a small error body; display allowlisted explanations, never raw messages.
    try:
        async with asyncio.timeout(3):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 4096:
                    return None
                body.extend(chunk)
            data = json.loads(body)
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if type(code) not in (str, int):
                return None
            code = str(code)
            if code in GLM_LIMIT_ERRORS:
                return GLM_LIMIT_ERRORS[code] + f"（{code}）" + NO_RETRY
    except (ValueError, httpx.HTTPError, TimeoutError):
        pass
    return None


async def _qwen_access_error(response: httpx.Response) -> str | None:
    """Map only documented codes; never relay arbitrary provider text."""
    try:
        async with asyncio.timeout(3):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 4096:
                    return None
                body.extend(chunk)
            data = json.loads(body)
            error = data.get("error") if isinstance(data, dict) else None
            code = data.get("code") if isinstance(data, dict) else None
            if code is None and isinstance(error, dict):
                code = error.get("code")
            if code == "AllocationQuota.FreeTierOnly":
                return (
                    "当前模型的免费 Token 已用完，并且百炼开启了“免费额度用完即停”。"
                    "请角色主人充值、关闭该开关，或私聊发送 /ai 模型 手动选择其他模型；不会自动切换。"
                )
    except (ValueError, httpx.HTTPError, TimeoutError):
        pass
    return None


async def complete(provider: str, region: str, key: str, messages: list[dict], *, model: str = "",
                   transport: httpx.AsyncBaseTransport | None = None) -> Completion:
    config = provider_for(provider, region)
    selected_model = model_for(provider, region, model)
    payload = {"model": selected_model.code, "messages": messages, "stream": False,
               "max_tokens": MAX_REPLY_TOKENS}
    if provider == "glm":
        payload["thinking"] = {"type": "disabled"}
        payload["stream"] = True
    elif selected_model.code.startswith("qwen3."):
        # Keep role chat concise and avoid hidden reasoning-token costs.
        payload["enable_thinking"] = False
    read_timeout = GLM_READ_TIMEOUT if provider == "glm" else 35
    total_timeout = GLM_TOTAL_TIMEOUT if provider == "glm" else 45
    try:
        # A wall-clock deadline also bounds slow trickle responses. No retries,
        # redirects, environment proxy credentials, tools, or remote memory.
        async with asyncio.timeout(total_timeout):
            async with http_clients.client(f"ai:{provider}:{region}", timeout=httpx.Timeout(
                    connect=10, read=read_timeout, write=35, pool=10),
                    follow_redirects=False, trust_env=False, transport=transport) as client:
                async with client.stream("POST", config.url, json=payload,
                        headers={"Authorization": "Bearer " + key}) as response:
                    if provider == "qwen" and response.status_code == 403:
                        reason = await _qwen_access_error(response)
                        if reason:
                            raise ToolError(reason)
                    if response.status_code in {401, 403}:
                        raise ToolError("AI 密钥无效、地域不匹配或模型未开通。请密钥主人在私聊检查配置。")
                    if provider == "glm" and response.status_code == 429:
                        reason = await _glm_limit_error(response)
                        if reason:
                            raise ToolError(reason)
                    if response.status_code in {402, 429}:
                        raise ToolError("AI 余额或调用额度不足，或服务限流。请稍后再试；不会切换到别人的密钥。")
                    if response.status_code != 200:
                        raise ToolError("AI 服务暂时不可用，请稍后再试。不会自动重试或切换模型。")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if provider == "glm" and content_type == "text/event-stream":
                        text, usage = await _stream_result(response)
                    else:
                        # Also accept a complete JSON response if the provider
                        # ignores stream=true. Do not send partial text to QQ.
                        text, usage = await _json_result(response)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty content")
        # Never relay reasoning_content, tool_calls, metadata or provider errors.
        text = text.replace(key, "[密钥已隐藏]").strip()
        text = "".join(c for c in text if c.isprintable() or c == "\n")[:MAX_REPLY_CHARS]
        if not text.strip():
            raise ValueError("no printable content")
        known = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                    for k in ("prompt_tokens", "completion_tokens"))
        return Completion(text, usage["prompt_tokens"] if known else 0,
                          usage["completion_tokens"] if known else 0, known)
    except ToolError:
        raise
    except httpx.ConnectTimeout:
        raise ToolError("AI 连接超时（10 秒），请检查服务器网络；" + NO_RETRY) from None
    except httpx.ReadTimeout:
        raise ToolError(f"AI 等待回复超时（连续 {read_timeout} 秒未收到数据）；" + NO_RETRY) from None
    except httpx.TimeoutException:
        raise ToolError("AI 请求发送或连接排队超时；" + NO_RETRY) from None
    except TimeoutError:
        raise ToolError(f"AI 请求超过总时限（{total_timeout} 秒），已停止等待；" + NO_RETRY) from None
    except httpx.ConnectError:
        raise ToolError("AI 无法连接服务器，请检查 DNS、网络或 TLS 证书；" + NO_RETRY) from None
    except httpx.RemoteProtocolError:
        raise ToolError("AI 回复传输中断，未收到完整回复；" + NO_RETRY) from None
    except httpx.HTTPError:
        raise ToolError("AI 请求发生网络传输异常；" + NO_RETRY) from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise ToolError("AI 返回格式异常或没有可显示的回复，请稍后再试。") from None
