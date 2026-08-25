"""Shared behavior host for non-Pygame renderers such as Electron."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from queue import Empty
from typing import Any

from live2d_support.renderer_contract import audio_command, motion_command, normalize_renderer_fact
from live2d_support.shared_behavior import SharedLive2DBehavior, StartAudio


CommandEmitter = Callable[[dict[str, Any]], None]


class SharedRendererHost:
    """Adapt shared behavior to a command/fact transport without SDK imports."""

    def __init__(self, emit: CommandEmitter, behavior: SharedLive2DBehavior | None = None) -> None:
        self._emit = emit
        self._behavior = behavior or SharedLive2DBehavior()

    def start_emotion_segment(self, *, turn_id: str, segment_id: str, emotion: str, audio_path: str) -> bool:
        segment = self._behavior.start_emotion_segment(
            turn_id=turn_id, segment_id=segment_id, emotion=emotion, audio_path=audio_path,
        )
        if segment is None:
            return False
        command = motion_command(segment)
        if command is None:
            self._emit_audio(self._behavior.motion_rejected(segment.command_id), segment)
        else:
            self._emit(command)
        return True

    def handle_renderer_fact(self, message: Mapping[str, Any]) -> bool:
        data = message.get("data")
        if not isinstance(data, Mapping):
            return False
        if message.get("type") == "renderer_ready":
            self._behavior.set_capabilities(data.get("motion_groups", {}))
            return True
        normalized = normalize_renderer_fact(message)
        if normalized is None:
            return False
        fact, token = normalized
        active = self._behavior.active_command
        if fact == "motion_started":
            self._emit_audio(self._behavior.motion_started(token), active)
            return True
        if fact == "motion_rejected":
            self._emit_audio(self._behavior.motion_rejected(token), active)
            return True
        if fact == "motion_finished":
            return self._behavior.motion_finished(token)
        if fact == "audio_started":
            return self._behavior.audio_started(token)
        if fact == "audio_ended":
            return self._behavior.audio_ended(token)
        if fact == "command_failed":
            self._behavior.command_failed(token, str(data.get("phase") or "unknown"))
            return True
        return False

    def _emit_audio(self, command: StartAudio | None, segment) -> None:
        if command is not None and segment is not None:
            self._emit(audio_command(command, segment))


class SharedRendererService:
    """Queue adapter for bridge deployments; its policy remains in the host."""

    def __init__(self, intent_queue, renderer_fact_queue, command_queue) -> None:
        self._intents = intent_queue
        self._facts = renderer_fact_queue
        self._host = SharedRendererHost(command_queue.put)

    def run_once(self) -> int:
        handled = 0
        while True:
            try:
                fact = self._facts.get_nowait()
            except Empty:
                break
            handled += int(isinstance(fact, Mapping) and self._host.handle_renderer_fact(fact))
        while True:
            try:
                intent = self._intents.get_nowait()
            except Empty:
                break
            if not isinstance(intent, Mapping) or intent.get("type") != "emotion_segment":
                continue
            data = intent.get("data", {})
            if not isinstance(data, Mapping):
                continue
            handled += int(self._host.start_emotion_segment(
                turn_id=str(data.get("turn_id", "")), segment_id=str(data.get("segment_id", "")),
                emotion=str(data.get("emotion", "")), audio_path=str(data.get("audio_path", "")),
            ))
        return handled
