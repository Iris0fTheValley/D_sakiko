"""Runnable core wiring for Electron renderer mode, without window ownership."""
from __future__ import annotations

from queue import Queue

from live2d_support.electron_intent_adapter import ElectronIntentAdapter
from live2d_support.renderer_host import SharedRendererService


class ElectronRendererRuntime:
    """Owns only shared behavior queues; bridge/window stay replaceable mechanics."""
    def __init__(self, emotion_queue, audio_queue, is_text_generating_queue=None) -> None:
        self.intent_queue = Queue()
        self.renderer_fact_queue = Queue()
        self.command_queue = Queue()
        self.adapter = ElectronIntentAdapter(emotion_queue, audio_queue, self.intent_queue)
        self.service = SharedRendererService(self.intent_queue, self.renderer_fact_queue, self.command_queue)
        self._thinking_queue = is_text_generating_queue
        self._thinking_active: bool | None = None

    def pump_once(self) -> int:
        adapted = int(self.adapter.run_once())
        adapted += int(self._sync_thinking_fact())
        return adapted + self.service.run_once()

    def _sync_thinking_fact(self) -> bool:
        """Project the existing master queue state as a fact, never a timer."""
        if self._thinking_queue is None:
            return False
        active = not self._thinking_queue.empty()
        if active == self._thinking_active:
            return False
        self._thinking_active = active
        self.intent_queue.put({"type": "thinking_changed", "data": {"active": active}})
        return True
