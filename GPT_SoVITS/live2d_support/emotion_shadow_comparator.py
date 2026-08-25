"""Compare first-slice owner commands with the preserved Pygame baseline."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from queue import Empty
import time


class EmotionShadowComparator:
    """Correlate semantic decisions without executing shadow commands.

    Random indexes deliberately remain diagnostic-only during this phase: the
    baseline and shadow owners are separate until the authoritative cutover.
    The comparison proves the stable behavior contract (group, priority,
    position and expression); index equivalence becomes automatic once the
    baseline owner is removed.
    """

    def __init__(self, baseline_facts, owner_commands, report: Callable[[dict], None]) -> None:
        self._facts, self._commands, self._report = baseline_facts, owner_commands, report
        self._baseline: dict[str, Mapping[str, object]] = {}
        self._owner: dict[str, Mapping[str, object]] = {}

    def run_once(self) -> int:
        handled = 0
        while True:
            try:
                fact = self._facts.get_nowait()
            except Empty:
                break
            if isinstance(fact, Mapping) and fact.get("type") == "baseline_emotion_motion":
                data = fact.get("data")
                if isinstance(data, Mapping):
                    self._baseline[str(data.get("segment_id") or "")] = data
                    handled += self._compare(str(data.get("segment_id") or ""))
        while True:
            try:
                command = self._commands.get_nowait()
            except Empty:
                break
            if isinstance(command, Mapping) and command.get("type") == "play_motion":
                data = command.get("data")
                if isinstance(data, Mapping) and str(data.get("turn_id") or "") == "legacy-shadow":
                    self._owner[str(data.get("segment_id") or "")] = data
                    handled += self._compare(str(data.get("segment_id") or ""))
        return handled

    def _compare(self, segment_id: str) -> int:
        baseline, owner = self._baseline.get(segment_id), self._owner.get(segment_id)
        if baseline is None or owner is None:
            return 0
        self._baseline.pop(segment_id, None)
        self._owner.pop(segment_id, None)
        keys = ("group", "priority", "position", "expression_id")
        differences = {key: (baseline.get(key), owner.get(key)) for key in keys if baseline.get(key) != owner.get(key)}
        self._report({
            "type": "emotion_shadow_comparison",
            "data": {
                "segment_id": segment_id,
                "equivalent": not differences,
                "differences": differences,
                "baseline_index": baseline.get("index"),
                "owner_index": owner.get("index"),
            },
        })
        return 1

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        while not stop_event.is_set():
            if not self.run_once():
                time.sleep(poll_interval_seconds)
