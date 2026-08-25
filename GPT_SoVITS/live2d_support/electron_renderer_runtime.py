"""Runnable core wiring for Electron renderer mode, without window ownership."""
from __future__ import annotations

from queue import Queue

from live2d_support.electron_intent_adapter import ElectronIntentAdapter
from live2d_support.renderer_host import SharedRendererService


class ElectronRendererRuntime:
    """Owns only shared behavior queues; bridge/window stay replaceable mechanics."""
    def __init__(self, emotion_queue, audio_queue) -> None:
        self.intent_queue = Queue()
        self.renderer_fact_queue = Queue()
        self.command_queue = Queue()
        self.adapter = ElectronIntentAdapter(emotion_queue, audio_queue, self.intent_queue)
        self.service = SharedRendererService(self.intent_queue, self.renderer_fact_queue, self.command_queue)

    def pump_once(self) -> int:
        adapted = int(self.adapter.run_once())
        return adapted + self.service.run_once()
