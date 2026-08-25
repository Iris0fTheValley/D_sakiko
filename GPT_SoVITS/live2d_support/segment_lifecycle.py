"""Translate renderer runtime facts into shared segment lifecycle transitions."""
from __future__ import annotations

from collections.abc import Callable

from live2d_support.shared_behavior import SharedLive2DBehavior, StartAudio


AudioStarter = Callable[[StartAudio], bool]


class SharedSegmentLifecycle:
    """Renderer-neutral fact consumer used by both Pygame and Electron adapters."""

    def __init__(self, behavior: SharedLive2DBehavior, start_audio: AudioStarter) -> None:
        self._behavior = behavior
        self._start_audio = start_audio
        self._audio_was_busy = False

    def consume_motion_fact(self, fact: str, command_id: str) -> bool:
        if fact == "motion_started":
            return self._start_audio_from(self._behavior.motion_started(command_id))
        if fact == "motion_rejected":
            return self._start_audio_from(self._behavior.motion_rejected(command_id))
        if fact == "motion_finished":
            return self._behavior.motion_finished(command_id)
        if fact == "command_failed":
            return self._start_audio_from(self._behavior.command_failed(command_id, "motion_start"))
        return False

    def observe_audio_busy(self, is_busy: bool) -> bool:
        """Report the real renderer audio-idle edge without guessing duration."""
        active = self._behavior.active_command
        if self._audio_was_busy and not is_busy and active is not None:
            self._audio_was_busy = False
            return self._behavior.audio_ended(active.command_id)
        self._audio_was_busy = is_busy
        return False

    def _start_audio_from(self, command: StartAudio | bool | None) -> bool:
        if not isinstance(command, StartAudio):
            return bool(command)
        if self._start_audio(command):
            self._audio_was_busy = True
            return self._behavior.audio_started(command.command_id)
        self._behavior.command_failed(command.command_id, "audio_start")
        return False
