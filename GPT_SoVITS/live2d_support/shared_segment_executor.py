"""Thin Pygame-side executor for already-decided shared segment commands."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from live2d_support.shared_behavior import PlaySegment


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
