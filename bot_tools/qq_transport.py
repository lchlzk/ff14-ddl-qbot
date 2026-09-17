"""Reliable QQ transport plus per-account live connection management."""
import asyncio
import threading
from collections import defaultdict
from typing import Any

import httpx
from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.bot import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.compat import type_validate_python
from nonebot.drivers import Request, Response
from nonebot.log import logger

from .bot_credentials import BotCredentialStore


class ReliableQQAdapter(Adapter):
    def __init__(self, *args: Any, **kwargs: Any):
        self._runtime_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
        self._runtime_forward_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
        self._runtime_lock = asyncio.Lock()
        self._runtime_state_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _remember_runtime_task(
        self, app_id: str, task: asyncio.Task, *, forward: bool = False,
    ) -> None:
        with self._runtime_state_lock:
            existed = task in self._runtime_tasks[app_id]
            self._runtime_tasks[app_id].add(task)
            if forward:
                self._runtime_forward_tasks[app_id].add(task)
        if existed:
            return

        def discard(done: asyncio.Task) -> None:
            with self._runtime_state_lock:
                tasks = self._runtime_tasks.get(app_id)
                if tasks is not None:
                    tasks.discard(done)
                    if not tasks:
                        self._runtime_tasks.pop(app_id, None)
                forwards = self._runtime_forward_tasks.get(app_id)
                if forwards is not None:
                    forwards.discard(done)
                    if not forwards:
                        self._runtime_forward_tasks.pop(app_id, None)

        task.add_done_callback(discard)

    def _configured(self, bot_info: BotInfo) -> bool:
        with self._runtime_state_lock:
            return any(
                item.id == bot_info.id and item == bot_info
                for item in self.qq_config.qq_bots
            )

    async def run_bot_websocket(self, bot_info: BotInfo) -> None:
        """Start one bot and retry gateway discovery without restarting peers."""
        task = asyncio.current_task()
        if task is not None:
            self._remember_runtime_task(bot_info.id, task)
        delay = 5
        while self._configured(bot_info):
            await super().run_bot_websocket(bot_info)
            # The upstream method returns after creating shard tasks. Give
            # those tasks one loop turn to register with our per-bot tracker.
            await asyncio.sleep(0)
            with self._runtime_state_lock:
                forwarded = bool(self._runtime_forward_tasks.get(bot_info.id))
            if bot_info.id in self.bots or forwarded:
                return
            if not self._configured(bot_info):
                return
            logger.warning(
                "QQ bot {} did not connect; retrying gateway discovery in {} seconds",
                bot_info.id, delay,
            )
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)

    async def _forward_ws(self, bot: Bot, ws_url, shard: tuple[int, int]) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._remember_runtime_task(bot.bot_info.id, task, forward=True)
        await super()._forward_ws(bot, ws_url, shard)

    def _spawn_runtime_bot(self, bot_info: BotInfo) -> None:
        task = asyncio.create_task(
            self.run_bot_websocket(bot_info),
            name=f"qqbot-runtime-{bot_info.id}",
        )
        self._remember_runtime_task(bot_info.id, task)
        task.add_done_callback(self.tasks.discard)
        self.tasks.add(task)

    async def replace_runtime_bot(self, app_id: str, bot_info: BotInfo | None) -> None:
        """Atomically replace one configured account and its connection tasks."""
        async with self._runtime_lock:
            with self._runtime_state_lock:
                current = next(
                    (item for item in self.qq_config.qq_bots if item.id == app_id), None
                )
                active = bool(self._runtime_tasks.get(app_id) or app_id in self.bots)
            if current == bot_info and (bot_info is None or not bot_info.use_websocket or active):
                return

            # Remove the old config first so a retrying task cannot recreate a
            # connection while it is being cancelled.
            with self._runtime_state_lock:
                self.qq_config.qq_bots[:] = [
                    item for item in self.qq_config.qq_bots if item.id != app_id
                ]
                tasks = list(self._runtime_tasks.get(app_id, ()))
            current_task = asyncio.current_task()
            for task in tasks:
                if task is not current_task and not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(
                    *(task for task in tasks if task is not current_task),
                    return_exceptions=True,
                )
            connected = self.bots.get(app_id)
            if connected is not None:
                self.bot_disconnect(connected)

            if bot_info is None:
                return
            with self._runtime_state_lock:
                self.qq_config.qq_bots.append(bot_info)
            if bot_info.use_websocket:
                self._spawn_runtime_bot(bot_info)

    def runtime_snapshot(self) -> dict[str, set[str]]:
        with self._runtime_state_lock:
            loaded = {item.id for item in self.qq_config.qq_bots}
            webhook = {item.id for item in self.qq_config.qq_bots if not item.use_websocket}
            connecting = {
                app_id for app_id, tasks in self._runtime_tasks.items()
                if any(not task.done() for task in tuple(tasks))
            }
        return {
            "loaded": loaded,
            "connected": loaded.intersection(self.bots),
            "connecting": connecting,
            "webhook": webhook,
        }

    async def request(self, setup: Request) -> Response:
        # Retry only connection establishment / pool acquisition. Read/write
        # failures are ambiguous: QQ might already have accepted the message.
        # Reuse the exact prepared request so msg_id/msg_seq/body do not change;
        # never re-run the command, feedback insert, lottery, or authorization.
        for attempt in range(3):
            try:
                return await super().request(setup)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt == 2:
                    logger.warning("QQ connection failed after 3 attempts ({})", type(exc).__name__)
                    raise
                logger.warning("QQ connection failed; retry {}/2 ({})", attempt + 1, type(exc).__name__)
                await asyncio.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable")


class QQBotRuntime:
    """Reconcile encrypted credential rows with a running QQ adapter."""

    def __init__(self, adapter: ReliableQQAdapter, credentials: BotCredentialStore):
        self.adapter = adapter
        self.credentials = credentials
        self._state_lock = threading.RLock()
        self._revisions = {
            item["app_id"]: float(item["updated"])
            for item in credentials.metadata()
        }

    async def reconcile(self, app_id: str) -> None:
        desired = self.credentials.runtime_one(app_id)
        info = (
            type_validate_python(BotInfo, desired.adapter_config())
            if desired is not None else None
        )
        await self.adapter.replace_runtime_bot(app_id, info)
        metadata = next(
            (item for item in self.credentials.metadata() if item["app_id"] == app_id),
            None,
        )
        with self._state_lock:
            if metadata is None:
                self._revisions.pop(app_id, None)
            else:
                self._revisions[app_id] = float(metadata["updated"])

    def snapshot(self) -> dict[str, object]:
        result: dict[str, object] = self.adapter.runtime_snapshot()
        with self._state_lock:
            result["revisions"] = dict(self._revisions)
        return result
