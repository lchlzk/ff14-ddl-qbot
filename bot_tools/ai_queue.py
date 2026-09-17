"""Bounded, round-robin AI admission before any quota or paid call is reserved."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from .storage import ToolError


class _State:
    def __init__(self):
        self.waiting = OrderedDict()
        self.running = {}

    def drain(self, maximum, per_group):
        while sum(self.running.values()) < maximum:
            chosen = next((g for g in self.waiting if self.running.get(g, 0) < per_group), None)
            if chosen is None:
                return
            queue = self.waiting[chosen]
            future = queue.popleft()
            if queue:
                self.waiting.move_to_end(chosen)
            else:
                del self.waiting[chosen]
            if not future.done():
                self.running[chosen] = self.running.get(chosen, 0) + 1
                future.set_result(None)


class FairLimiter:
    def __init__(self, maximum=4, per_group=2, pending=64, group_pending=16, wait_seconds=30):
        self.maximum, self.per_group = maximum, per_group
        self.pending, self.group_pending = pending, group_pending
        self.wait_seconds = wait_seconds
        self.loops = WeakKeyDictionary()

    @asynccontextmanager
    async def slot(self, group):
        loop = asyncio.get_running_loop()
        state = self.loops.setdefault(loop, _State())
        can_start = (sum(state.running.values()) < self.maximum
                     and state.running.get(group, 0) < self.per_group)
        if not can_start and (sum(len(q) for q in state.waiting.values()) >= self.pending
                              or len(state.waiting.get(group, ())) >= self.group_pending):
            raise ToolError("AI 等待队列已满，请稍后再试；本次未调用模型、未扣额度。")
        future = loop.create_future()
        state.waiting.setdefault(group, deque()).append(future)
        state.drain(self.maximum, self.per_group)
        try:
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=self.wait_seconds)
            except TimeoutError:
                raise ToolError("AI 排队超过 30 秒，请稍后再试；本次未调用模型、未扣额度。") from None
            yield
        finally:
            if future.done() and not future.cancelled():
                state.running[group] -= 1
                if not state.running[group]:
                    del state.running[group]
            else:
                future.cancel()
                queue = state.waiting.get(group)
                if queue and future in queue:
                    queue.remove(future)
                    if not queue:
                        del state.waiting[group]
            state.drain(self.maximum, self.per_group)
