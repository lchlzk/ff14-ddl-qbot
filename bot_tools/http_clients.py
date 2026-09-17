"""Loop-scoped connection pools; credentials always belong to one request."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
from weakref import WeakKeyDictionary

import httpx


class NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


_pools: WeakKeyDictionary = WeakKeyDictionary()


@asynccontextmanager
async def client(tag: str, **options):
    # Mock/custom transports are isolated and closed at the end of their test.
    if options.get("transport") is not None:
        async with httpx.AsyncClient(**options) as isolated:
            yield isolated
        return
    pools = _pools.setdefault(asyncio.get_running_loop(), {})
    pooled = pools.get(tag)
    if pooled is None or pooled.is_closed:
        pooled = httpx.AsyncClient(**options, cookies=CookieJar(policy=NoCookies()),
                                  limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
        pools[tag] = pooled
    yield pooled


async def close() -> None:
    pools = _pools.pop(asyncio.get_running_loop(), {})
    await asyncio.gather(*(pooled.aclose() for pooled in pools.values()))
