"""Clock-driven master-Pygame behavior decisions, independent of renderers."""
from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Callable, Iterable, Mapping

from live2d_support.expression_policy import select_expression_for_motion


@dataclass(frozen=True)
class ScheduledMotion:
    group: str
    index: int
    priority: int
    purpose: str
    expression_id: str | None = None


class SharedBehaviorScheduler:
    """Owns master timers; adapters supply facts and execute returned commands."""

    def __init__(self, *, clock: Callable[[], float], rng: Random | None = None) -> None:
        self._clock, self._rng = clock, rng or Random()
        self._catalog: dict[str, int] = {}
        self._resolved_groups: dict[str, str] = {}
        self._motion_files: dict[str, tuple[str, ...]] = {}
        self._expression_ids: frozenset[str] = frozenset()
        now = clock()
        self._thinking = False
        self._think_motion_over = True
        self._thinking_due: float | None = None
        self._motion_over = True
        self._audio_busy = False
        self._idle_recover_due = now + 2.5
        self._timed_idle_due = now + 25.0
        self._long_group = ""
        self._long_enabled = False
        self._long_due: float | None = None
        self._long_repeats = 0

    def set_catalog(self, catalog: Mapping[str, int]) -> None:
        self._catalog = {str(group): int(count) for group, count in catalog.items() if int(count) > 0}
        self._resolved_groups = {}
        for group in self._catalog:
            base, separator, suffix = group.rpartition("_")
            if separator and suffix == "C":
                self._resolved_groups[base] = group
        for group in self._catalog:
            self._resolved_groups.setdefault(group, group)

    def set_model_catalog(self, motion_files_by_group: Mapping[str, Iterable[str]], expression_ids: Iterable[str] = ()) -> None:
        self._motion_files = {str(group): tuple(str(path) for path in files) for group, files in motion_files_by_group.items()}
        self._expression_ids = frozenset(str(value) for value in expression_ids)
        self.set_catalog({group: len(files) for group, files in self._motion_files.items()})

    def set_thinking(self, active: bool) -> None:
        if active == self._thinking:
            return
        self._thinking = active
        if active:
            self._thinking_due = self._clock() + (1.0 if self._think_motion_over else 15.0)
        else:
            self._thinking_due = None

    def set_audio_busy(self, busy: bool) -> None:
        self._audio_busy = busy
        if not busy:
            self._long_enabled = False
            self._long_due = None

    def set_motion_over(self, motion_over: bool) -> None:
        """Receive the renderer's actual callback-derived motion fact."""
        if motion_over and not self._motion_over:
            self._idle_recover_due = self._clock() + 2.5
            if self._long_enabled and self._audio_busy:
                self._long_due = self._clock() + 2.5
        self._motion_over = motion_over

    def start_segment(self, group: str, audio_duration_seconds: float) -> None:
        self._thinking = False
        self._thinking_due = None
        self._motion_over = False
        self._long_group = group
        self._long_enabled = audio_duration_seconds >= 6.0
        self._long_due = None
        self._long_repeats = 0

    def motion_started(self, purpose: str) -> None:
        if purpose == "thinking":
            self._think_motion_over = False
        else:
            self._motion_over = False

    def motion_finished(self, purpose: str) -> None:
        now = self._clock()
        if purpose == "thinking":
            self._think_motion_over = True
            if self._thinking:
                self._thinking_due = now + 15.0
            return
        self._motion_over = True
        self._idle_recover_due = now + 2.5
        if purpose in {"emotion", "long_audio_repeat"} and self._long_enabled and self._audio_busy:
            self._long_due = now + 2.5

    def click(self, *, is_sakiko: bool) -> ScheduledMotion | None:
        self._think_motion_over = True
        return self._exact("IDLE", 1, "click") if is_sakiko else None

    def tick(self) -> ScheduledMotion | None:
        now = self._clock()
        if self._long_due is not None and now >= self._long_due:
            self._long_due = None
            if self._audio_busy and self._motion_over and self._long_repeats < 2:
                command = self._exact(self._long_group, 3, "long_audio_repeat")
                if command is not None:
                    self._long_repeats += 1
                    return command
        if self._thinking and self._think_motion_over and self._thinking_due is not None and now >= self._thinking_due:
            command = self._exact("text_generating", 3, "thinking")
            if command is not None:
                self._thinking_due = now + 15.0
                return command
        if self._motion_over and not self._audio_busy and not self._thinking and now >= self._idle_recover_due:
            return self._exact("idle_motion", 1, "idle_recover")
        if not self._audio_busy and not self._thinking and now >= self._timed_idle_due:
            self._timed_idle_due = now + 25.0
            return self._exact("IDLE", 1, "timed_idle")
        return None

    def timed_idle_due(self) -> ScheduledMotion | None:
        """Master's 25-second IDLE check; always advances its deadline."""
        now = self._clock()
        if now < self._timed_idle_due:
            return None
        self._timed_idle_due = now + 25.0
        if self._audio_busy or self._thinking:
            return None
        return self._exact("IDLE", 1, "timed_idle")

    def idle_recover_due(self) -> ScheduledMotion | None:
        if not self._motion_over or self._audio_busy or self._thinking:
            return None
        if self._clock() < self._idle_recover_due:
            return None
        return self._exact("idle_motion", 1, "idle_recover")

    def long_audio_due(self) -> ScheduledMotion | None:
        now = self._clock()
        if self._long_due is None or now < self._long_due:
            return None
        self._long_due = None
        if not self._audio_busy or not self._motion_over or self._long_repeats >= 2:
            return None
        command = self._exact(self._long_group, 3, "long_audio_repeat")
        if command is not None:
            self._long_repeats += 1
        return command

    def _exact(self, group: str, priority: int, purpose: str) -> ScheduledMotion | None:
        resolved_group = self._resolved_groups.get(group, group)
        count = self._catalog.get(resolved_group, 0)
        if count <= 0:
            return None
        index = self._rng.randrange(count)
        motion_file = self._motion_files.get(resolved_group, (None,) * count)[index]
        return ScheduledMotion(
            resolved_group, index, priority, purpose,
            select_expression_for_motion(resolved_group, motion_file, self._expression_ids),
        )
