"""Bounded public-response caches and single-flight request coalescing."""
from __future__ import annotations

import asyncio
import functools
import sys
from collections import OrderedDict
from weakref import WeakKeyDictionary


def memory_size(value, seen=None) -> int:
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(memory_size(k, seen) + memory_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(memory_size(item, seen) for item in value)
    return size


class ResponseCache(OrderedDict):
    def __init__(self, max_bytes=16 * 1024 * 1024, max_entries=200):
        super().__init__()
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.sizes = {}
        self.bytes = 0

    def __setitem__(self, key, value):
        self.pop(key, None)
        size = memory_size((key, value))
        if size > self.max_bytes:
            return
        while self and (self.bytes + size > self.max_bytes or len(self) >= self.max_entries):
            self.pop(next(iter(self)))
        super().__setitem__(key, value)
        self.sizes[key] = size
        self.bytes += size

    def pop(self, key, default=None):
        if key in self:
            self.bytes -= self.sizes.pop(key)
        return super().pop(key, default)

    def clear(self):
        super().clear()
        self.sizes.clear()
        self.bytes = 0


def singleflight(key_for):
    """Public read-only requests only. Never wrap paid AI calls/mutations."""
    def decorate(function):
        loops = WeakKeyDictionary()

        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            pending = loops.setdefault(asyncio.get_running_loop(), {})
            key = key_for(*args, **kwargs)
            task = pending.get(key)
            if task is None:
                task = asyncio.create_task(function(*args, **kwargs))
                pending[key] = task

                def done(completed):
                    if pending.get(key) is completed:
                        pending.pop(key, None)
                    if not completed.cancelled():
                        completed.exception()  # Observe failures if every waiter cancelled.
                task.add_done_callback(done)
            # Cancelling one caller must not cancel other groups' shared read.
            return await asyncio.shield(task)
        return wrapped
    return decorate
