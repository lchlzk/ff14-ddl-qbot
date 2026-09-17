"""Mask private binding invitations and AI configuration before log sinks."""
import re
from contextvars import ContextVar


_INVITATION = re.compile(r"(?i)(/bot\s+whoami\s+)[a-f0-9]{32}\b")
_AI_COMMAND = re.compile(r"(?i)/ai(?:\b|\\[nrt])")
_LIKELY_KEY = re.compile(r"(?i)\bsk-[a-z0-9_-]{16,}\b")
active_ai_key: ContextVar[str] = ContextVar("active_ai_key", default="")


def redact_binding_log(record: dict) -> None:
    # Called before log sinks, including NoneBot's pre-dispatch event log.
    message = record["message"]
    if _AI_COMMAND.search(message) or ("Loaded" in message and "Config" in message and record.get("function") == "init"):
        # Mask the ENTIRE event/debug line, not just sk-* tokens: GLM uses a
        # different key format, and raw adapter event reprs duplicate content.
        record["message"] = "[配置/授权指令内容已隐藏]"
        record["exception"] = None
        record["extra"] = {k: v for k, v in record.get("extra", {}).items() if k == "nonebot_log_level"}
        return
    key = active_ai_key.get()
    if key:
        message = message.replace(key, "[AI 密钥已隐藏]")
        # Exception diagnostics can print local variables containing headers.
        record["exception"] = None
        record["extra"] = {k: v for k, v in record.get("extra", {}).items() if k == "nonebot_log_level"}
    message = _LIKELY_KEY.sub("[疑似密钥已隐藏]", message)
    record["message"] = _INVITATION.sub(r"\1[授权码已隐藏]", message)


def initialize_nonebot_safely(**kwargs):
    """NoneBot.init replaces the global patcher. Cover init AND later events."""
    import nonebot
    from nonebot.log import logger
    previous = nonebot.logger
    nonebot.logger = previous.patch(redact_binding_log)
    try:
        nonebot.init(**kwargs)
    finally:
        nonebot.logger = previous
        logger.configure(patcher=redact_binding_log)
