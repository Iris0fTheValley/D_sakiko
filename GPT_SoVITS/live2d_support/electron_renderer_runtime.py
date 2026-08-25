"""Runnable core wiring for Electron renderer mode, without window ownership."""
from __future__ import annotations

from queue import Empty, Queue

from live2d_support.electron_intent_adapter import ElectronIntentAdapter
from live2d_support.renderer_host import SharedRendererService
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner


class ElectronRendererRuntime:
    """Owns only shared behavior queues; bridge/window stay replaceable mechanics."""
    def __init__(self, emotion_queue, audio_queue, owner: AuthoritativeLive2DOwner, thinking_state_events=None) -> None:
        self.intent_queue = Queue()
        self.renderer_fact_queue = Queue()
        self.command_queue = Queue()
        self.adapter = ElectronIntentAdapter(emotion_queue, audio_queue, self.intent_queue)
        self.service = SharedRendererService(self.intent_queue, self.renderer_fact_queue, self.command_queue, owner)
        self._thinking_events = thinking_state_events

    def pump_once(self) -> int:
        adapted = int(self.adapter.run_once())
        adapted += self._drain_thinking_facts()
        return adapted + self.service.run_once()

    def _drain_thinking_facts(self) -> int:
        if self._thinking_events is None:
            return 0
        handled = 0
        while True:
            try:
                event = self._thinking_events.get_nowait()
            except Empty:
                return handled
            if isinstance(event, dict) and event.get("type") == "thinking_changed":
                self.intent_queue.put(event)
                handled += 1
