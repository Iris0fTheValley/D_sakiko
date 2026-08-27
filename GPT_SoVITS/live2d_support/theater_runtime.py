"""Standalone small-theater wiring for the shared authoritative owner."""
from __future__ import annotations

import threading

from live2d_support.authoritative_owner import AuthoritativeLive2DOwner
from live2d_support.renderer_host import SharedRendererService
from live2d_support.runtime_ingress import TheaterIngressAdapter


def _run_theater_pygame_backend(*args) -> None:
    # Keep owner wiring importable for configuration and topology checks that
    # do not install the optional Pygame/Live2D runtime.
    from live2d_support.theater_pygame_backend import run_theater_pygame_backend
    run_theater_pygame_backend(*args)


class _TheaterTurnEventSink:
    def __init__(self, text_queue, turn_queue) -> None:
        self._text_queue = text_queue
        self._turn_queue = turn_queue

    def put(self, turn) -> None:
        text = str(turn.get("text") or "")
        translation = str(turn.get("translation") or "")
        self._text_queue.put({
            "character_name": str(turn.get("character_name") or ""),
            "text": text + ("\n" + translation if translation else ""),
        })
        self._turn_queue.put(turn)


class TheaterRuntime:
    """Own one owner/service and one mechanics-only Pygame backend process."""

    def __init__(
        self,
        *,
        context,
        control_queue,
        playlist_queue,
        turn_event_queue,
        desktop_width: int,
        desktop_height: int,
        log_queue,
    ) -> None:
        self._stop_event = threading.Event()
        self._intent_queue = context.Queue()
        self._fact_queue = context.Queue()
        self._command_queue = context.Queue()
        self._pygame_control_queue = context.Queue()
        self._text_queue = context.Queue()
        self._legacy_emotion_queue = context.Queue()
        self._legacy_audio_queue = context.Queue()
        self._thinking_queue = context.Queue()
        self._legacy_conversion_queue = context.Queue()
        self.motion_complete_value = context.Value("b", True)
        self.is_display_text_value = context.Value("b", True)

        self.owner = AuthoritativeLive2DOwner()
        self.service = SharedRendererService(
            self._intent_queue,
            self._fact_queue,
            self._command_queue,
            self.owner,
            self.motion_complete_value,
            theater_event_queue=_TheaterTurnEventSink(self._text_queue, turn_event_queue),
        )
        self.ingress = TheaterIngressAdapter(control_queue, playlist_queue, self._intent_queue)
        self.process = context.Process(
            target=_run_theater_pygame_backend,
            args=(
                self._fact_queue,
                self._command_queue,
                self._text_queue,
                self.is_display_text_value,
                self.motion_complete_value,
                desktop_width,
                desktop_height,
                log_queue,
            ),
            name="Live2DProcess",
        )
        self._service_thread = threading.Thread(
            target=self.service.run, args=(self._stop_event,), daemon=True,
            name="TheaterLive2DOwner",
        )
        self._ingress_thread = threading.Thread(
            target=self.ingress.run, args=(self._stop_event,), daemon=True,
            name="TheaterLive2DIngress",
        )

    def start(self) -> None:
        self.process.start()
        self._service_thread.start()
        self._ingress_thread.start()

    def shutdown(self, timeout_seconds: float = 8.0) -> None:
        self._intent_queue.put({"type": "bye", "data": {}})
        self.service.wait_for_bye_completion(timeout_seconds)
        self._stop_event.set()
        self._service_thread.join(timeout=2.0)
        self._ingress_thread.join(timeout=2.0)
        self.process.join(timeout=3.0)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=3.0)


def create_theater_runtime(**kwargs) -> TheaterRuntime:
    return TheaterRuntime(**kwargs)
