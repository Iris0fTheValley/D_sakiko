"""Thin Pygame-side executor for already-decided shared segment commands."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from live2d_support.shared_behavior import PlaySegment
from live2d_support.behavior_scheduler import ScheduledMotion


class ExactMotionRuntime(Protocol):
    def StartMotion(self, group_name: str, motion_index: int, priority: int, on_start=None,
                    on_finish=None, position=None, auto_expression: bool = True) -> bool: ...

    def set_expression_if_supported(self, expression_id: str) -> bool: ...


FactEmitter = Callable[[str, str], None]


class PygameSharedSegmentExecutor:
    """Execute one exact command without choosing motion or expression again."""

    def __init__(self, runtime: ExactMotionRuntime, emit_fact: FactEmitter) -> None:
        self._runtime = runtime
        self._emit_fact = emit_fact

    def execute(self, command: PlaySegment) -> bool:
        motion = command.motion
        if motion is None:
            self._emit_fact("motion_rejected", command.command_id)
            return False
        if motion.expression_id is not None:
            self._runtime.set_expression_if_supported(motion.expression_id)
        started = self._runtime.StartMotion(
            motion.group,
            motion.index,
            motion.priority,
            lambda *args: self._emit_fact("motion_started", command.command_id),
            lambda *args: self._emit_fact("motion_finished", command.command_id),
            position=None,
            auto_expression=False,
        )
        if not started:
            self._emit_fact("motion_rejected", command.command_id)
        return started


class PygameScheduledMotionExecutor:
    """Execute a scheduler-owned exact motion with adapter-only callbacks."""

    def __init__(self, runtime: ExactMotionRuntime) -> None:
        self._runtime = runtime

    def execute(self, command: ScheduledMotion, on_start: Callable[..., object] | None, on_finish: Callable[..., object] | None) -> bool:
        if command.expression_id is not None:
            self._runtime.set_expression_if_supported(command.expression_id)
        return self._runtime.StartMotion(
            command.group,
            command.index,
            command.priority,
            on_start,
            on_finish,
            position=None,
            auto_expression=False,
        )


class PygameRendererCommandAdapter:
    """Execute only exact owner commands and return mechanical lifecycle facts."""

    def __init__(self, runtime: ExactMotionRuntime, emit_fact: Callable[[dict], None],
                 start_audio: Callable[[str], bool] | None = None) -> None:
        self._runtime, self._emit_fact, self._start_audio = runtime, emit_fact, start_audio

    def execute(self, command: Mapping[str, object]) -> bool:
        data = command.get("data")
        if not isinstance(data, Mapping): return False
        if command.get("type") == "play_audio":
            return self._execute_audio(data)
        if command.get("type") != "play_motion": return False
        token, group, index = str(data.get("token") or ""), str(data.get("group") or ""), data.get("index")
        if not token or not group or not isinstance(index, int):
            self._emit_fact({"type":"command_failed","data":{"token":token,"phase":"motion_start"}}); return False
        expression = data.get("expression_id")
        if isinstance(expression, str) and expression: self._runtime.set_expression_if_supported(expression)
        def started(*_): self._emit_fact({"type":"motion_started","data":{"token":token}})
        def finished(*_): self._emit_fact({"type":"motion_finished","data":{"token":token}})
        ok = self._runtime.StartMotion(group, index, int(data.get("priority", 3)), started, finished, position=None, auto_expression=False)
        if not ok: self._emit_fact({"type":"command_failed","data":{"token":token,"phase":"motion_start"}})
        return ok

    def _execute_audio(self, data: Mapping[str, object]) -> bool:
        token, path = str(data.get("token") or ""), str(data.get("path") or "")
        if not token or not path or self._start_audio is None or not self._start_audio(path):
            self._emit_fact({"type": "command_failed", "data": {"token": token, "phase": "audio_start"}})
            return False
        self._emit_fact({"type": "audio_started", "data": {"token": token}})
        return True
