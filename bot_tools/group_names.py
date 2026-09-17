"""Best-effort QQ group-name discovery without exposing group OpenIDs to the UI."""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import quote

from nonebot.adapters.qq import Bot
from nonebot.drivers import Request
from nonebot.log import logger

from .storage import Store, clean


REFRESH_INTERVAL = 24 * 60 * 60
_recent: dict[tuple[str, str], tuple[float, str]] = {}


def _candidate(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("data")
    if isinstance(nested, dict):
        payload = nested
    value = payload.get("group_name")
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return clean(value, 40)
    except ValueError:
        return ""


def claim(store: Store, scope: str, now: float | None = None) -> bool:
    """Claim one refresh slot so multiple matchers never fan out API calls."""
    now = time.time() if now is None else now
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO group_metadata(scope) VALUES(?)", (scope,))
        row = db.execute(
            "SELECT retry_after FROM group_metadata WHERE scope=?", (scope,)
        ).fetchone()
        if row and row["retry_after"] > now:
            return False
        db.execute(
            "UPDATE group_metadata SET retry_after=? WHERE scope=?",
            (now + REFRESH_INTERVAL, scope),
        )
        return True


def record(store: Store, scope: str, *, official_name: str = "", error: str = "",
           now: float | None = None) -> None:
    now = time.time() if now is None else now
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO group_metadata(scope) VALUES(?)", (scope,))
        if official_name:
            db.execute(
                "UPDATE group_metadata SET official_name=?,checked=?,last_error='' WHERE scope=?",
                (official_name, now, scope),
            )
        else:
            db.execute(
                "UPDATE group_metadata SET checked=?,last_error=? WHERE scope=?",
                (now, error[:120], scope),
            )


async def refresh(store: Store, bot: Bot, group_openid: str, scope: str,
                  event_name: str = "") -> None:
    """Use an event name when available, otherwise try QQ's restricted info API."""
    cache_key = (str(store.path.resolve()), scope)
    recent = _recent.get(cache_key)
    name = _candidate({"group_name": event_name})
    if recent and recent[0] > time.monotonic() and (not name or recent[1] == name):
        return
    if len(_recent) >= 4096:
        _recent.pop(next(iter(_recent)))
    # Mark in-flight before awaiting, so simultaneous group messages coalesce.
    _recent[cache_key] = (time.monotonic() + 60, name)
    try:
        if event_name:
            name = _candidate({"group_name": event_name})
            if name:
                await asyncio.to_thread(record, store, scope, official_name=name)
                return
        if not await asyncio.to_thread(claim, store, scope):
            return
        headers = await bot.get_authorization_header()
        base = str(bot.adapter.get_api_base()).rstrip("/")
        url = f"{base}/v2/groups/{quote(group_openid, safe='')}/info"
        response = await bot.adapter.request(Request("GET", url, headers=headers, timeout=6))
        payload = json.loads(response.content or b"{}")
        name = _candidate(payload)
        if response.status_code == 200 and name:
            await asyncio.to_thread(record, store, scope, official_name=name)
            return
        code = payload.get("code") if isinstance(payload, dict) else None
        suffix = f"，错误码 {code}" if isinstance(code, (str, int)) else ""
        await asyncio.to_thread(record, store, scope, error=f"腾讯群资料接口不可用（HTTP {response.status_code}{suffix}）")
    except Exception as exc:
        # Group-name discovery is cosmetic and must never delay or break replies.
        try:
            await asyncio.to_thread(record, store, scope, error="腾讯群资料查询暂时失败")
        except Exception:
            pass
        logger.debug("QQ group-name lookup skipped: {}", type(exc).__name__)
