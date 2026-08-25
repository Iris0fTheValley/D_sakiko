"""Renderer-independent Live2D behaviour decisions.

This module deliberately starts with the Pygame emotion/audio segment path.  It
does not load a model or play media: adapters report capabilities and execution
facts, while this core makes the group/index/priority decision exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Mapping
from uuid import uuid4

from live2d_support.motion_semantics import motion_group_for_emotion


@dataclass(frozen=True)
class MotionCapability:
    """Ordered motions available to a renderer for one group."""
    group: str
    count: int


@dataclass(frozen=True)
class ExactMotion:
    """A fully decided motion; an adapter must not choose another one."""
    group: str
    index: int
    priority: int = 3
    position: str = "C"


@dataclass(frozen=True)
class PlaySegment:
    """Atomic decision for one audio-backed emotion segment."""
    command_id: str
    turn_id: str
    segment_id: str
    motion: ExactMotion
    audio_path: str
    audio_duration_seconds: float


class SharedLive2DBehavior:
    """Small authoritative state for the first migrated behaviour slice."""

    def __init__(self, *, rng: Random | None = None) -> None:
        self._rng = rng or Random()
        self._capabilities: dict[str, MotionCapability] = {}
        self._active_command: PlaySegment | None = None
        self._motion_active = False
        self._audio_active = False

    @property
    def legacy_motion_complete(self) -> bool:
        """Compatibility projection of Pygame's ``not mixer.get_busy()``."""
        return not self._audio_active

    @property
    def active_command(self) -> PlaySegment | None:
        return self._active_command

    def set_capabilities(self, motions: Mapping[str, int]) -> None:
        self._capabilities = {
            str(group): MotionCapability(str(group), int(count))
            for group, count in motions.items()
            if int(count) > 0
        }

    def start_emotion_segment(
        self, *, turn_id: str, segment_id: str, emotion: str,
        audio_path: str, audio_duration_seconds: float = 0.0,
    ) -> PlaySegment | None:
        """Resolve master Pygame's emotion mapping to one exact motion.

        The Pygame compatibility boundary dequeues its paired audio *before*
        resolving this mapping.  Thus an unknown label produces no command,
        while that boundary still preserves the source FIFO-consumption order.
        """
        group = motion_group_for_emotion(emotion, default="")
        capability = self._capabilities.get(group)
        if not group or capability is None:
            return None
        command = PlaySegment(
            command_id=uuid4().hex,
            turn_id=turn_id,
            segment_id=segment_id,
            motion=ExactMotion(group, self._rng.randrange(capability.count)),
            audio_path=audio_path,
            audio_duration_seconds=max(0.0, audio_duration_seconds),
        )
        self._active_command = command
        self._motion_active = False
        self._audio_active = False
        return command

    def motion_started(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._motion_active = True
        return True

    def motion_rejected(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._motion_active = False
        return True

    def motion_finished(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._motion_active = False
        return True

    def audio_started(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._audio_active = True
        return True

    def audio_ended(self, command_id: str) -> bool:
        if not self._matches(command_id):
            return False
        self._audio_active = False
        self._active_command = None
        return True

    def command_failed(self, command_id: str, phase: str) -> bool:
        """Normalize an adapter failure into a non-blocking lifecycle fact."""
        if not self._matches(command_id):
            return False
        if phase == "motion_start":
            self._motion_active = False
            return True
        self._audio_active = False
        self._active_command = None
        return True

    def _matches(self, command_id: str) -> bool:
        return self._active_command is not None and self._active_command.command_id == command_id
