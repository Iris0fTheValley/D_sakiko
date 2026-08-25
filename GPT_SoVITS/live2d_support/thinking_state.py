"""Mirror master thinking FIFO edges without changing its queue contract."""
from __future__ import annotations

from queue import Empty


class ThinkingStateQueue:
    """Delegate the legacy queue while publishing only 0<->1 activity edges."""

    def __init__(self, queue, state_events, count) -> None:
        self._queue = queue
        self._events = state_events
        self._count = count

    def put(self, item, *args, **kwargs) -> None:
        self._queue.put(item, *args, **kwargs)
        self._change(1)

    def get(self, *args, **kwargs):
        item = self._queue.get(*args, **kwargs)
        self._change(-1)
        return item

    def get_nowait(self):
        item = self._queue.get_nowait()
        self._change(-1)
        return item

    def empty(self) -> bool:
        return self._queue.empty()

    def _change(self, delta: int) -> None:
        lock = self._count.get_lock()
        with lock:
            before = self._count.value
            self._count.value = max(0, before + delta)
            after = self._count.value
        if before == 0 and after > 0:
            self._events.put({"type": "thinking_changed", "data": {"active": True}})
        elif before > 0 and after == 0:
            self._events.put({"type": "thinking_changed", "data": {"active": False}})
