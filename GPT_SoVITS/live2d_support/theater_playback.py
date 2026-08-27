"""Authoritative small-theater playlist state.

The Qt window still produces its historical queue payloads.  This state lives
inside :class:`AuthoritativeLive2DOwner`, so renderer processes never choose a
speaker, model, motion direction, delay, or cancellation policy.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Mapping
import time


class TheaterPlaybackState:
    TURN_GAP_SECONDS = 0.5
    NO_AUDIO_SECONDS_PER_CHARACTER = 0.1

    def __init__(self, *, clock=None) -> None:
        self._clock = clock or time.monotonic
        self._slots: list[dict[str, Any]] = []
        self._pending: deque[dict[str, Any]] = deque()
        self._active: dict[str, Any] | None = None
        self._active_token = ""
        self._active_dispatched = False
        self._last_speaker_slot = 1
        self._stage_ready = False
        self._stage_switch_pending = False
        self._next_turn_at = 0.0
        self._sequence = 0
        self._motion_facing_mode = "screen"
        self._idle_switch_slot: int | None = None
        self._changed_slot: int | None = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def awaiting_model(self) -> bool:
        return self._stage_switch_pending

    @property
    def active_token(self) -> str:
        return self._active_token

    def set_active_slots(self, data: Mapping[str, Any]) -> dict[str, Any] | None:
        slots = data.get("slots")
        if not isinstance(slots, list):
            return None
        normalized = [dict(slot) for slot in slots if isinstance(slot, Mapping)]
        if not normalized:
            return None
        self._slots = normalized
        changed_slot = data.get("changed_slot")
        self._changed_slot = changed_slot if changed_slot in (0, 1) else None
        self._motion_facing_mode = str(data.get("motion_facing_mode") or "screen")
        if not bool(data.get("preserve_playback", False)):
            self._pending.clear()
        self._stage_ready = False
        if self._active is not None:
            self._idle_switch_slot = 0
            return None
        self._idle_switch_slot = None
        return self._request_stage_switch()

    def enqueue_playlist(self, data: Mapping[str, Any]) -> None:
        playlist = data.get("playlist")
        if not isinstance(playlist, list):
            return
        if not bool(data.get("preserve_playback", False)):
            self._pending.clear()
        self._pending.extend(
            deepcopy(dict(turn)) for turn in playlist if isinstance(turn, Mapping)
        )

    def stop_after_current_turn(self) -> None:
        self._pending.clear()

    def cancel(self) -> None:
        self._pending.clear()
        self._active = None
        self._active_token = ""
        self._active_dispatched = False
        self._stage_switch_pending = False
        self._next_turn_at = 0.0

    def next_model_switch(self) -> dict[str, Any] | None:
        if self._active is not None:
            return None
        if self._idle_switch_slot is not None:
            self._idle_switch_slot = None
            return self._request_stage_switch()
        if not self._pending or self._clock() < self._next_turn_at:
            return None
        self._active = self._pending.popleft()
        self._active_token = ""
        self._active_dispatched = False
        self._speaker_slot(str(self._active.get("character_name") or ""))
        return None

    def accept_model_ready(self) -> None:
        self._stage_ready = True
        self._stage_switch_pending = False

    def ready_turn(self) -> dict[str, Any] | None:
        if self._active is None or self._active_dispatched or self.awaiting_model:
            return None
        return deepcopy(self._active)

    def mark_dispatched(self, token: str) -> None:
        self._active_dispatched = True
        self._active_token = str(token)

    def complete(self, token: str = "") -> bool:
        if self._active is None:
            return False
        if token and self._active_token and token != self._active_token:
            return False
        duration = float(self._active.get("audio_duration_seconds", 0.0) or 0.0)
        text = str(self._active.get("text") or "")
        delay = self.TURN_GAP_SECONDS
        if duration <= 0.0:
            delay += self.NO_AUDIO_SECONDS_PER_CHARACTER * len(text)
        self._next_turn_at = self._clock() + delay
        self._active = None
        self._active_token = ""
        self._active_dispatched = False
        return True

    def update_sakiko_model(self, *, black: bool) -> None:
        model_json = (
            "../live2d_related/sakiko/live2D_model_costume/3.model.json"
            if black else "../live2d_related/sakiko/live2D_model/3.model.json"
        )
        for slot in self._slots:
            if str(slot.get("character_name") or "") == "祥子" or str(
                slot.get("character_folder_name") or ""
            ).lower() == "sakiko":
                slot["model_json_path"] = model_json
        self._stage_ready = False
        if self._active is not None:
            self._idle_switch_slot = 0

    def _speaker_slot(self, speaker_name: str) -> int:
        for index, slot in enumerate(self._slots):
            if speaker_name == str(slot.get("character_name") or ""):
                self._last_speaker_slot = index
                return index
        return min(self._last_speaker_slot, max(0, len(self._slots) - 1))

    def _request_stage_switch(self) -> dict[str, Any] | None:
        if not self._slots:
            return None
        if self._stage_ready and not self._stage_switch_pending:
            return None
        self._stage_switch_pending = True
        return {
            "type": "switch_live2d",
            "theater_slots": deepcopy(self._slots),
            "initial_model": True,
            "changed_slot": self._changed_slot,
        }

    def segment_data(self, turn: Mapping[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        slot_index = self._speaker_slot(str(turn.get("character_name") or ""))
        position = "C"
        if self._motion_facing_mode == "face_to_face":
            position = "L" if slot_index == 0 else "R"
        return {
            "turn_id": str(turn.get("turn_uid") or f"theater-{self._sequence}"),
            "segment_id": str(self._sequence),
            "emotion": str(turn.get("emotion") or "normal"),
            "audio_path": str(turn.get("audio_path") or ""),
            "audio_duration_seconds": float(turn.get("audio_duration_seconds", 0.0) or 0.0),
            "position": position,
            "target_slot": slot_index,
        }
