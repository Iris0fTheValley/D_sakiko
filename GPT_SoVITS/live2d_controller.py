"""Renderer-independent Live2D behavior state machine.

The controller owns *behavior*: motion selection, priorities, timers, segment
lifecycle, and stale-event rejection.  A renderer owns *mechanics*: loading a
model, invoking its SDK with the exact group/index/priority supplied here,
playing audio, applying lip sync, and reporting lifecycle facts.

Commands are emitted once as protocol-compatible dictionary envelopes.  A
bridge may broadcast that one envelope to any number of renderers.  Renderer
facts are fed back through :meth:`handle_renderer_event`; the controller never
imports or guesses the behavior of a rendering SDK.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import random
import re
import threading
import time
import uuid
from typing import Any, Callable, Deque, Dict, Mapping, Optional, Set

try:
    from live2d_support.expression_policy import (
        select_expression_for_motion,
    )
    from live2d_support.motion_capabilities import motion_files_by_group_from_model_json
    from live2d_support.motion_semantics import motion_group_for_emotion
except ModuleNotFoundError:  # Support importing as GPT_SoVITS.live2d_controller in tests.
    from GPT_SoVITS.live2d_support.expression_policy import (
        select_expression_for_motion,
    )
    from GPT_SoVITS.live2d_support.motion_capabilities import motion_files_by_group_from_model_json
    from GPT_SoVITS.live2d_support.motion_semantics import motion_group_for_emotion


PROTOCOL_VERSION = 1

@dataclass(frozen=True)
class BehaviorConfig:
    """Timing policy in seconds; all deadlines use the injected clock."""

    idle_recover_delay: float = 2.5
    timed_idle_interval: float = 25.0
    thinking_first_delay: float = 1.0
    thinking_repeat_delay: float = 15.0
    long_audio_threshold: float = 6.0
    long_audio_repeat_delay: float = 2.5
    long_audio_max_repeats: int = 2
    model_switch_timeout: float = 30.0
    click_throttle: float = 0.2


@dataclass
class _Motion:
    token: str
    command_event_id: str
    cause_event_id: str
    group: str
    index: int
    priority: int
    purpose: str
    turn_id: str
    segment_id: str
    expected_renderers: Set[str] = field(default_factory=set)
    started_renderers: Set[str] = field(default_factory=set)
    finished_renderers: Set[str] = field(default_factory=set)


@dataclass
class _Audio:
    token: str
    command_event_id: str
    cause_event_id: str
    url: str
    turn_id: str
    segment_id: str
    expected_renderers: Set[str] = field(default_factory=set)
    started_renderers: Set[str] = field(default_factory=set)
    ended_renderers: Set[str] = field(default_factory=set)


@dataclass
class _Segment:
    event_id: str
    token: str
    turn_id: str
    segment_id: str
    motion_group: str
    audio_url: str
    audio_duration: float
    text: str
    audio_token: str = ""
    first_motion_finished: bool = False
    long_repeat_count: int = 0
    long_repeat_due: Optional[float] = None
    completed: bool = False


class Live2DBehaviorController:
    """Single source of truth for Live2D behavior.

    ``emit`` receives one dictionary envelope per command.  It is deliberately
    not called once per renderer; the surrounding bridge performs fan-out.

    ``motion_catalog`` maps a motion group to its number of valid indices.  The
    controller will not emit a motion for an unknown/empty group.  Renderers may
    publish their actual catalog in ``renderer_ready.data.motion_groups``; with
    multiple ready renderers, only the common safe index range is used.
    """

    def __init__(
        self,
        emit: Callable[[Dict[str, Any]], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
        id_factory: Optional[Callable[[], str]] = None,
        session_id: Optional[str] = None,
        motion_catalog: Optional[Mapping[str, int]] = None,
        config: Optional[BehaviorConfig] = None,
    ) -> None:
        if not callable(emit):
            raise TypeError("emit must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(wall_clock):
            raise TypeError("wall_clock must be callable")

        self._emit_callback = emit
        self._clock = clock
        self._wall_clock = wall_clock
        self._rng = rng or random.Random()
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._config = config or BehaviorConfig()
        self._base_catalog = self._normalize_catalog(motion_catalog or {})

        self._lock = threading.RLock()
        self._outbox: Deque[Dict[str, Any]] = deque()
        self._flushing = False
        self._closed = False
        self._seq = 0
        self._token_seq = 0
        self._session_id = str(session_id or self._id_factory())

        self._renderers: Dict[str, Dict[str, Any]] = {}
        self._seen_event_ids: Set[str] = set()
        self._seen_event_order: Deque[str] = deque()
        self._seen_event_limit = 2048

        now = self._clock()
        self._state = "idle"
        self._thinking = False
        self._thinking_turn_id = ""
        self._thinking_due: Optional[float] = None
        self._idle_recover_due = now + self._config.idle_recover_delay
        self._timed_idle_due = now + self._config.timed_idle_interval

        self._motion: Optional[_Motion] = None
        self._audio: Optional[_Audio] = None
        self._segment: Optional[_Segment] = None

        self._model: Dict[str, Any] = {}
        self._confirmed_model: Dict[str, Any] = {}
        self._model_token = ""
        self._model_cause_event_id = ""
        self._model_turn_id = ""
        self._model_expected: Set[str] = set()
        self._model_ready: Set[str] = set()
        self._model_failed: Set[str] = set()
        self._model_transition_groups = ("change_character",)
        self._model_transition_priority = 3
        self._model_deadline: Optional[float] = None
        self._motion_files_by_group: Dict[str, tuple[str, ...]] = {}
        self._confirmed_motion_files_by_group: Dict[str, tuple[str, ...]] = {}
        self._bye_event_id = ""
        self._sakiko_mask_on: Optional[bool] = None
        self._last_click_at = float("-inf")
        self._current_expression: Optional[str] = None

    # ------------------------------------------------------------------
    # Public business intents

    def tick(self) -> None:
        """Advance timer-driven behavior using the injected monotonic clock."""
        with self._lock:
            if self._closed:
                return
            now = self._clock()

            if (
                self._state == "switching"
                and self._model_token
                and self._model_deadline is not None
                and now >= self._model_deadline
            ):
                self._abort_model_switch_locked("timeout")

            segment = self._segment
            if (
                segment is not None
                and not segment.completed
                and segment.long_repeat_due is not None
                and now >= segment.long_repeat_due
                and self._audio is not None
                and self._motion is None
                and segment.long_repeat_count < self._config.long_audio_max_repeats
            ):
                segment.long_repeat_due = None
                token = self._request_motion_locked(
                    segment.motion_group,
                    3,
                    "long_audio_repeat",
                    segment.event_id,
                    segment.turn_id,
                    segment.segment_id,
                )
                if token:
                    segment.long_repeat_count += 1

            if (
                self._thinking
                and self._segment is None
                and self._motion is None
                and self._thinking_due is not None
                and now >= self._thinking_due
            ):
                self._request_motion_locked(
                    "text_generating",
                    3,
                    "thinking",
                    self._new_event_id(),
                    self._thinking_turn_id,
                    "",
                )
                self._thinking_due = now + self._config.thinking_repeat_delay

            if self._can_idle_locked() and now >= self._timed_idle_due:
                self._timed_idle_due = now + self._config.timed_idle_interval
                self._request_motion_locked(
                    "IDLE", 1, "timed_idle", self._new_event_id(), "", ""
                )
            elif self._can_idle_locked() and now >= self._idle_recover_due:
                self._request_motion_locked(
                    "idle_motion", 1, "idle_recover", self._new_event_id(), "", ""
                )
        self._flush()

    def start_thinking(self, turn_id: str, *, event_id: Optional[str] = None) -> str:
        """Enter thinking mode; the first motion is timer-driven."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            if self._state == "switching":
                return ""
            self._thinking = True
            self._thinking_turn_id = str(turn_id)
            self._thinking_due = self._clock() + self._config.thinking_first_delay
            if self._segment is None:
                self._state = "thinking"
            self._queue_command_locked(
                "thinking_changed",
                {"active": True, "turn_id": str(turn_id), "cause_event_id": cause},
            )
        self._flush()
        return cause

    def stop_thinking(self, turn_id: str = "", *, event_id: Optional[str] = None) -> str:
        """Leave thinking mode and invalidate an active thinking motion."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            self._thinking = False
            self._thinking_turn_id = ""
            self._thinking_due = None
            if self._motion is not None and self._motion.purpose == "thinking":
                self._cancel_motion_locked("thinking_stopped", cause)
            if self._segment is None and self._state != "switching":
                self._state = "idle"
            self._idle_recover_due = self._clock() + self._config.idle_recover_delay
            self._queue_command_locked(
                "thinking_changed",
                {"active": False, "turn_id": str(turn_id), "cause_event_id": cause},
            )
        self._flush()
        return cause

    def set_theme_color(self, theme_color: str) -> str:
        """Broadcast a renderer-only theme update without changing behavior state."""
        color = str(theme_color or "").strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise ValueError("theme_color must be a #RRGGBB value")
        with self._lock:
            self._ensure_open_locked()
            event_id = self._queue_command_locked(
                "set_theme_color",
                {
                    "theme_color": color,
                    "target_renderer_ids": sorted(self._renderers),
                },
            )
        self._flush()
        return event_id

    def start_emotion_segment(
        self,
        *,
        turn_id: str,
        segment_id: str,
        emotion: str = "",
        motion_group: str = "",
        audio_url: str = "",
        audio_duration: float = 0.0,
        text: str = "",
        event_id: Optional[str] = None,
    ) -> str:
        """Start one assistant segment and select its first motion exactly once."""
        cause = str(event_id or self._new_event_id())
        group = str(motion_group or motion_group_for_emotion(str(emotion), default=""))
        with self._lock:
            self._ensure_open_locked()
            if self._state == "switching":
                return ""
            self._cancel_segment_locked("superseded", cause)
            self._thinking = False
            self._thinking_turn_id = ""
            self._thinking_due = None
            self._state = "speaking"

            segment = _Segment(
                event_id=cause,
                token=self._new_token_locked("segment"),
                turn_id=str(turn_id),
                segment_id=str(segment_id),
                motion_group=group,
                audio_url=str(audio_url),
                audio_duration=max(0.0, float(audio_duration)),
                text=str(text),
            )
            self._segment = segment

            motion_token = ""
            if group:
                motion_token = self._request_motion_locked(
                    group, 3, "emotion", cause, segment.turn_id, segment.segment_id
                )
            if audio_url:
                segment.audio_token = self._request_audio_locked(segment)

            self._queue_command_locked(
                "segment_started",
                {
                    "cause_event_id": cause,
                    "token": segment.token,
                    "turn_id": segment.turn_id,
                    "segment_id": segment.segment_id,
                    "emotion": str(emotion),
                    "motion_group": group,
                    "motion_token": motion_token,
                    "audio_token": segment.audio_token,
                    "text": segment.text,
                },
            )

            if not audio_url and not motion_token:
                self._complete_segment_locked("no_playback")
        self._flush()
        return cause

    def click_motion(self, *, event_id: Optional[str] = None) -> Optional[str]:
        """Request one user-click IDLE motion when no higher priority work exists."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            if self._state == "switching":
                return None
            now = self._clock()
            if now - self._last_click_at < self._config.click_throttle:
                token = ""
            else:
                self._last_click_at = now
                token = self._request_motion_locked("IDLE", 1, "click", cause, "", "")
        self._flush()
        return token or None

    def special_motion(
        self,
        groups: tuple[str, ...] | list[str],
        *,
        priority: int = 3,
        purpose: str = "special",
        turn_id: str = "",
        event_id: Optional[str] = None,
    ) -> Optional[str]:
        """Choose and start one explicit special motion in the behavior layer.

        Renderers only execute the resulting group/index.  In particular this
        keeps mask transitions deterministic across multiple Electron windows.
        """
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            catalog = self._effective_catalog_locked()
            available = [str(group) for group in groups if catalog.get(str(group), 0) > 0]
            if not available:
                token = ""
            else:
                group = self._rng.choice(available)
                token = self._request_motion_locked(
                    group, int(priority), str(purpose), cause, str(turn_id), ""
                )
        self._flush()
        return token or None

    def toggle_sakiko_mask(self, *, event_id: Optional[str] = None) -> Optional[str]:
        """Request the next Sakiko mask transition without renderer-side RNG."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            if self._state == "switching":
                return None
            if self._sakiko_mask_on is True:
                groups = ("change_character_maskoff",)
            elif self._sakiko_mask_on is False:
                groups = ("maskon",)
            else:
                groups = ("change_character_maskoff", "maskon")
            catalog = self._effective_catalog_locked()
            available = [group for group in groups if catalog.get(group, 0) > 0]
            group = self._rng.choice(available) if available else ""
            token = self._request_motion_locked(group, 3, "maskoff", cause, "", "") if group else ""
            if token:
                self._sakiko_mask_on = group == "change_character_maskoff"
        self._flush()
        return token or None

    def switch_model(
        self,
        model: Mapping[str, Any],
        *,
        turn_id: str = "",
        event_id: Optional[str] = None,
    ) -> str:
        """Emit a model-load intent; renderer_ready confirms actual readiness."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            self._cancel_segment_locked("model_switch", cause)
            self._cancel_motion_locked("model_switch", cause)
            self._state = "switching"
            self._model = dict(model)
            self._motion_files_by_group = self._read_motion_files_locked(model)
            self._sakiko_mask_on = None
            raw_transition_groups = model.get("transition_groups", ("change_character",))
            if isinstance(raw_transition_groups, (list, tuple)):
                transition_groups = tuple(
                    str(group) for group in raw_transition_groups if str(group)
                )
            else:
                transition_groups = (str(raw_transition_groups),) if raw_transition_groups else ()
            self._model_transition_groups = transition_groups or ("change_character",)
            try:
                self._model_transition_priority = int(model.get("transition_priority", 3))
            except (TypeError, ValueError):
                self._model_transition_priority = 3
            self._model_token = self._new_token_locked("model")
            self._model_cause_event_id = cause
            self._model_turn_id = str(turn_id)
            self._model_expected = set(self._renderers)
            self._model_ready.clear()
            self._model_failed.clear()
            self._model_deadline = self._clock() + self._config.model_switch_timeout
            self._queue_command_locked(
                "load_model",
                {
                    "cause_event_id": cause,
                    "token": self._model_token,
                    "turn_id": str(turn_id),
                    "model": dict(model),
                    "target_renderer_ids": sorted(self._model_expected),
                },
            )
        self._flush()
        return self._model_token

    def bye(self, *, turn_id: str = "", event_id: Optional[str] = None) -> str:
        """Enter terminal behavior and close renderers after the bye motion."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            self._bye_event_id = cause
            self._thinking = False
            self._thinking_due = None
            self._cancel_segment_locked("bye", cause)
            self._cancel_motion_locked("bye", cause)
            self._state = "exiting"
            token = self._request_motion_locked("bye", 3, "bye", cause, str(turn_id), "")
            if not token:
                self._queue_close_renderer_locked(cause, "bye_motion_unavailable")
        self._flush()
        return cause

    # ------------------------------------------------------------------
    # Renderer facts

    def handle_renderer_event(self, message: Mapping[str, Any]) -> bool:
        """Consume a renderer fact; return False for invalid/stale/duplicate facts."""
        if not isinstance(message, Mapping):
            return False
        msg_type = message.get("type")
        raw_data = message.get("data", {})
        if not isinstance(msg_type, str) or not isinstance(raw_data, Mapping):
            return False
        data = dict(raw_data)
        event_id = str(message.get("event_id") or data.get("event_id") or "")
        session_id = str(message.get("session_id") or data.get("session_id") or "")

        with self._lock:
            if self._closed:
                return False
            if session_id and session_id != self._session_id:
                return False
            if event_id and not self._remember_event_locked(event_id):
                return False

            renderer_id = str(data.get("renderer_id") or message.get("source") or "default")
            handled = False
            if msg_type == "renderer_ready":
                handled = self._on_renderer_ready_locked(renderer_id, data)
            elif msg_type == "renderer_disconnected":
                handled = self._on_renderer_disconnected_locked(renderer_id, data)
            elif msg_type == "motion_started":
                handled = self._on_motion_started_locked(renderer_id, data)
            elif msg_type == "motion_finished":
                handled = self._on_motion_finished_locked(renderer_id, data)
            elif msg_type == "audio_started":
                handled = self._on_audio_started_locked(renderer_id, data)
            elif msg_type == "audio_ended":
                handled = self._on_audio_ended_locked(renderer_id, data)
            elif msg_type == "mouth_amplitude":
                handled = self._on_mouth_amplitude_locked(renderer_id, data)
            elif msg_type == "command_failed" and data.get("command_type") == "load_model":
                handled = self._on_model_failed_locked(renderer_id, data)
            elif msg_type in {"motion_failed", "audio_failed"}:
                handled = self._on_renderer_failure_locked(msg_type, renderer_id, data)
            else:
                return False
        self._flush()
        return handled

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable diagnostic snapshot."""
        with self._lock:
            return {
                "session_id": self._session_id,
                "state": self._state,
                "closed": self._closed,
                "thinking": self._thinking,
                "thinking_turn_id": self._thinking_turn_id,
                "renderer_ids": sorted(self._renderers),
                "motion_catalog": self._effective_catalog_locked(),
                "model": dict(self._model),
                "model_token": self._model_token,
                "active_motion": self._motion_snapshot_locked(),
                "active_audio": self._audio_snapshot_locked(),
                "active_segment": self._segment_snapshot_locked(),
                "deadlines": {
                    "thinking": self._thinking_due,
                    "idle_recover": self._idle_recover_due,
                    "timed_idle": self._timed_idle_due,
                    "model_switch": self._model_deadline,
                    "long_audio_repeat": (
                        self._segment.long_repeat_due if self._segment is not None else None
                    ),
                },
                "seq": self._seq,
            }

    def reset(self, *, event_id: Optional[str] = None) -> str:
        """Invalidate pending work while preserving renderer registrations."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            self._ensure_open_locked()
            self._cancel_segment_locked("reset", cause)
            self._cancel_motion_locked("reset", cause)
            self._thinking = False
            self._thinking_turn_id = ""
            self._thinking_due = None
            self._state = "idle"
            self._bye_event_id = ""
            self._model_token = ""
            self._model_cause_event_id = ""
            self._model_turn_id = ""
            self._model_expected.clear()
            self._model_ready.clear()
            self._model_failed.clear()
            self._model_deadline = None
            now = self._clock()
            self._idle_recover_due = now + self._config.idle_recover_delay
            self._timed_idle_due = now + self._config.timed_idle_interval
            self._queue_command_locked(
                "reset_renderer",
                {"cause_event_id": cause, "target_renderer_ids": sorted(self._renderers)},
            )
        self._flush()
        return cause

    def close(self, *, event_id: Optional[str] = None) -> None:
        """Stop accepting work and emit one controller shutdown command."""
        cause = str(event_id or self._new_event_id())
        with self._lock:
            if self._closed:
                return
            self._cancel_segment_locked("controller_closed", cause)
            self._cancel_motion_locked("controller_closed", cause)
            self._queue_command_locked(
                "controller_closed",
                {"cause_event_id": cause, "target_renderer_ids": sorted(self._renderers)},
            )
            self._closed = True
            self._state = "closed"
        self._flush()

    # ------------------------------------------------------------------
    # Renderer event handlers

    def _on_renderer_ready_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        token = str(data.get("token") or data.get("model_token") or "")
        if self._state == "switching" and self._model_token and token != self._model_token:
            return False
        catalog = self._normalize_catalog(data.get("motion_groups", {}))
        capabilities = data.get("capabilities", {})
        previous = self._renderers.get(renderer_id)
        connection_id = str(data.get("connection_id") or "")
        replacement = bool(
            previous
            and connection_id
            and str(previous.get("connection_id") or "")
            and str(previous.get("connection_id")) != connection_id
        )
        if replacement and self._audio is not None and renderer_id in self._audio.expected_renderers:
            # The old socket cannot produce audio_ended anymore.  End its
            # ownership before registering the replacement socket.
            self._audio.ended_renderers.add(renderer_id)
            self._audio.expected_renderers.discard(renderer_id)
            self._maybe_finish_audio_locked(reason="renderer_reconnected")
        renderer_info = {
            "motion_groups": catalog,
            "expression_ids": self._normalize_expression_ids(data.get("expression_ids", ())),
            "capabilities": dict(capabilities) if isinstance(capabilities, Mapping) else {},
            "connection_id": connection_id,
            "model_token": token,
        }
        if previous and previous.get("pending_model_token"):
            renderer_info["pending_model_token"] = previous["pending_model_token"]
        self._renderers[renderer_id] = renderer_info

        if self._state != "switching" or not self._model_token:
            pending_model_token = str(renderer_info.get("pending_model_token") or "")
            if self._confirmed_model and not token and not pending_model_token:
                pending_model_token = self._new_token_locked("model-restore")
                renderer_info["pending_model_token"] = pending_model_token
                self._queue_command_locked(
                    "load_model",
                    {
                        "cause_event_id": self._model_cause_event_id or self._new_event_id(),
                        "token": pending_model_token,
                        "turn_id": self._model_turn_id,
                        "model": dict(self._confirmed_model),
                        "target_renderer_ids": [renderer_id],
                    },
                )
                return True
            if pending_model_token and token == pending_model_token:
                renderer_info.pop("pending_model_token", None)
            motion = self._motion
            if motion is not None and (renderer_id not in motion.expected_renderers or replacement):
                motion.expected_renderers.add(renderer_id)
                self._queue_command_locked(
                    "play_motion",
                    {
                        "cause_event_id": motion.cause_event_id,
                        "token": motion.token,
                        "turn_id": motion.turn_id,
                        "segment_id": motion.segment_id,
                        "group": motion.group,
                        "index": motion.index,
                        "priority": motion.priority,
                        "purpose": motion.purpose,
                        "target_renderer_ids": [renderer_id],
                    },
                )
            if self._current_expression is not None:
                # Replayed motion may reset the SDK expression, so restore it
                # after the motion command for a renderer joining mid-segment.
                self._queue_expression_locked(
                    self._current_expression,
                    self._model_cause_event_id or self._new_event_id(),
                    self._model_turn_id,
                    "",
                    target_renderer_ids=[renderer_id],
                )
            return True
        self._model_ready.add(renderer_id)
        expected = self._model_expected or {renderer_id}
        if expected.issubset(self._model_ready | self._model_failed):
            if self._model_failed:
                self._abort_model_switch_locked("renderer_failed")
            else:
                self._finish_model_switch_locked()
        return True

    def _on_renderer_disconnected_locked(self, renderer_id: str, data: Mapping[str, Any]) -> bool:
        if renderer_id not in self._renderers:
            return False
        connection_id = str(data.get("connection_id") or "")
        current_connection_id = str(self._renderers[renderer_id].get("connection_id") or "")
        if connection_id and current_connection_id and connection_id != current_connection_id:
            # A stale socket can close after a replacement socket has already
            # re-registered the same renderer id.
            return False
        self._renderers.pop(renderer_id, None)
        if self._motion is not None:
            self._motion.finished_renderers.add(renderer_id)
            self._motion.expected_renderers.discard(renderer_id)
            self._maybe_finish_motion_locked()
        if self._audio is not None:
            self._audio.ended_renderers.add(renderer_id)
            self._audio.expected_renderers.discard(renderer_id)
            self._maybe_finish_audio_locked(reason="renderer_disconnected")
        self._model_expected.discard(renderer_id)
        if self._state == "switching" and self._model_token and not self._model_expected:
            self._abort_model_switch_locked("renderer_unavailable")
            return True
        if (
            self._state == "switching"
            and self._model_token
            and self._model_expected
            and self._model_expected.issubset(self._model_ready)
        ):
            self._finish_model_switch_locked()
        return True

    def _on_model_failed_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        token = str(data.get("token") or data.get("model_token") or "")
        if self._state != "switching" or not self._model_token or token != self._model_token:
            return False
        if self._model_expected and renderer_id not in self._model_expected:
            return False
        self._model_failed.add(renderer_id)
        expected = self._model_expected or {renderer_id}
        if expected.issubset(self._model_ready | self._model_failed):
            self._abort_model_switch_locked("renderer_failed")
        return True

    def _on_motion_started_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        motion = self._matching_motion_locked(data)
        if motion is None or not self._renderer_expected(renderer_id, motion.expected_renderers):
            return False
        motion.started_renderers.add(renderer_id)
        return True

    def _on_motion_finished_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        motion = self._matching_motion_locked(data)
        if motion is None or not self._renderer_expected(renderer_id, motion.expected_renderers):
            return False
        motion.finished_renderers.add(renderer_id)
        self._maybe_finish_motion_locked()
        return True

    def _on_audio_started_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        audio = self._matching_audio_locked(data)
        if audio is None or not self._renderer_expected(renderer_id, audio.expected_renderers):
            return False
        audio.started_renderers.add(renderer_id)
        return True

    def _on_audio_ended_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        audio = self._matching_audio_locked(data)
        if audio is None or not self._renderer_expected(renderer_id, audio.expected_renderers):
            return False
        audio.ended_renderers.add(renderer_id)
        self._maybe_finish_audio_locked()
        return True

    def _on_mouth_amplitude_locked(self, renderer_id: str, data: Dict[str, Any]) -> bool:
        """Fan out the audio owner's RMS sample to every renderer.

        This is a renderer-local visual value, not a behavior transition.  The
        audio owner is the only renderer allowed to produce the sample and the
        only one that receives ``play_audio``.
        """
        audio = self._matching_audio_locked(data)
        if audio is None or renderer_id not in audio.expected_renderers:
            return False
        try:
            amplitude = max(0.0, min(1.0, float(data.get("amplitude", 0.0))))
        except (TypeError, ValueError):
            return False
        self._queue_command_locked(
            "mouth_amplitude",
            {
                "token": audio.token,
                "turn_id": audio.turn_id,
                "segment_id": audio.segment_id,
                "amplitude": amplitude,
                "target_renderer_ids": sorted(self._renderers),
            },
        )
        return True

    def _on_renderer_failure_locked(
        self, msg_type: str, renderer_id: str, data: Dict[str, Any]
    ) -> bool:
        if msg_type == "motion_failed":
            motion = self._matching_motion_locked(data)
            if motion is None or not self._renderer_expected(renderer_id, motion.expected_renderers):
                return False
            motion.finished_renderers.add(renderer_id)
            self._maybe_finish_motion_locked()
            return True
        audio = self._matching_audio_locked(data)
        if audio is None or not self._renderer_expected(renderer_id, audio.expected_renderers):
            return False
        audio.ended_renderers.add(renderer_id)
        self._maybe_finish_audio_locked(reason="audio_failed")
        return True

    # ------------------------------------------------------------------
    # State transitions

    def _finish_model_switch_locked(self) -> None:
        cause = self._model_cause_event_id or self._new_event_id()
        turn_id = self._model_turn_id
        transition_groups = self._model_transition_groups
        transition_priority = self._model_transition_priority
        self._confirmed_model = dict(self._model)
        self._confirmed_motion_files_by_group = dict(self._motion_files_by_group)
        self._model_token = ""
        self._model_cause_event_id = ""
        self._model_turn_id = ""
        self._model_expected.clear()
        self._model_ready.clear()
        self._model_failed.clear()
        self._model_deadline = None
        self._state = "thinking" if self._thinking else "idle"
        self._idle_recover_due = self._clock() + self._config.idle_recover_delay
        catalog = self._effective_catalog_locked()
        available_groups = [group for group in transition_groups if catalog.get(group, 0) > 0]
        if available_groups:
            group = (
                available_groups[0]
                if len(available_groups) == 1
                else self._rng.choice(available_groups)
            )
            self._request_motion_locked(
                group, transition_priority, "model_switch", cause, turn_id, ""
            )
            if str(self._model.get("variant") or "") == "dark":
                self._sakiko_mask_on = group == "change_character"
        if not available_groups:
            self._queue_expression_locked(self._default_expression_locked(), cause, turn_id, "")

    def _abort_model_switch_locked(self, reason: str) -> None:
        cause = self._model_cause_event_id or self._new_event_id()
        failed_model = dict(self._model)
        self._model = dict(self._confirmed_model)
        self._motion_files_by_group = dict(self._confirmed_motion_files_by_group)
        self._model_token = ""
        self._model_cause_event_id = ""
        self._model_turn_id = ""
        self._model_expected.clear()
        self._model_ready.clear()
        self._model_failed.clear()
        self._model_deadline = None
        self._state = "thinking" if self._thinking else "idle"
        now = self._clock()
        self._idle_recover_due = now + self._config.idle_recover_delay
        self._timed_idle_due = now + self._config.timed_idle_interval
        self._queue_command_locked(
            "model_switch_failed",
            {
                "cause_event_id": cause,
                "reason": reason,
                "model": failed_model,
                "target_renderer_ids": sorted(self._renderers),
            },
        )

    def _request_motion_locked(
        self,
        group: str,
        priority: int,
        purpose: str,
        cause_event_id: str,
        turn_id: str,
        segment_id: str,
    ) -> str:
        if self._state == "switching":
            return ""
        count = self._effective_catalog_locked().get(group, 0)
        if count <= 0:
            return ""
        if self._motion is not None:
            if priority < self._motion.priority:
                return ""
            if priority == self._motion.priority and purpose in {"idle_recover", "timed_idle"}:
                return ""
            self._motion = None  # the new token makes late facts from the old motion stale

        if not self._renderers:
            return ""
        token = self._new_token_locked("motion")
        index = self._rng.randrange(count)
        expected = set(self._renderers)
        command_event_id = self._queue_command_locked(
            "play_motion",
            {
                "cause_event_id": cause_event_id,
                "token": token,
                "turn_id": turn_id,
                "segment_id": segment_id,
                "group": group,
                "index": index,
                "priority": int(priority),
                "purpose": purpose,
                "target_renderer_ids": sorted(expected),
            },
        )
        self._motion = _Motion(
            token=token,
            command_event_id=command_event_id,
            cause_event_id=cause_event_id,
            group=group,
            index=index,
            priority=int(priority),
            purpose=purpose,
            turn_id=turn_id,
            segment_id=segment_id,
            expected_renderers=expected,
        )
        # One common path resolves every motion's expression from the same
        # model metadata and policy used by the Pygame adapter.
        self._queue_expression_locked(
            self._expression_for_motion_locked(group, index),
            cause_event_id,
            turn_id,
            segment_id,
        )
        return token

    def _request_audio_locked(self, segment: _Segment) -> str:
        if not self._renderers:
            return ""
        audio_renderers = {
            renderer_id
            for renderer_id, info in self._renderers.items()
            if bool(info.get("capabilities", {}).get("audio", False))
        }
        if not audio_renderers and self._renderers:
            audio_renderers = {sorted(self._renderers)[0]}
        owner = sorted(audio_renderers)[0] if audio_renderers else ""
        expected = {owner} if owner else set()
        token = self._new_token_locked("audio")
        command_event_id = self._queue_command_locked(
            "play_audio",
            {
                "cause_event_id": segment.event_id,
                "token": token,
                "segment_token": segment.token,
                "turn_id": segment.turn_id,
                "segment_id": segment.segment_id,
                "url": segment.audio_url,
                "duration": segment.audio_duration,
                "target_renderer_id": owner,
            },
        )
        self._audio = _Audio(
            token=token,
            command_event_id=command_event_id,
            cause_event_id=segment.event_id,
            url=segment.audio_url,
            turn_id=segment.turn_id,
            segment_id=segment.segment_id,
            expected_renderers=expected,
        )
        return token

    def _maybe_finish_motion_locked(self) -> None:
        motion = self._motion
        if motion is None or not self._facts_complete(
            motion.expected_renderers, motion.finished_renderers
        ):
            return
        self._motion = None
        now = self._clock()
        self._idle_recover_due = now + self._config.idle_recover_delay

        if motion.purpose == "bye":
            self._queue_close_renderer_locked(motion.cause_event_id, "bye_motion_finished")
            return
        if motion.purpose == "model_switch":
            self._queue_expression_locked(
                self._default_expression_locked(),
                motion.cause_event_id,
                motion.turn_id,
                motion.segment_id,
            )
        if motion.purpose == "thinking" and self._thinking:
            self._thinking_due = now + self._config.thinking_repeat_delay

        segment = self._segment
        if (
            segment is not None
            and motion.turn_id == segment.turn_id
            and motion.segment_id == segment.segment_id
        ):
            if motion.purpose == "emotion":
                segment.first_motion_finished = True
            if self._audio is None:
                self._complete_segment_locked("motion_finished")
            elif (
                segment.audio_duration >= self._config.long_audio_threshold
                and segment.long_repeat_count < self._config.long_audio_max_repeats
            ):
                segment.long_repeat_due = now + self._config.long_audio_repeat_delay

    def _maybe_finish_audio_locked(self, reason: str = "audio_ended") -> None:
        audio = self._audio
        if audio is None or not self._facts_complete(audio.expected_renderers, audio.ended_renderers):
            return
        self._queue_command_locked(
            "mouth_amplitude",
            {
                "token": audio.token,
                "turn_id": audio.turn_id,
                "segment_id": audio.segment_id,
                "amplitude": 0.0,
                "target_renderer_ids": sorted(self._renderers),
            },
        )
        self._audio = None
        segment = self._segment
        if (
            segment is not None
            and audio.turn_id == segment.turn_id
            and audio.segment_id == segment.segment_id
        ):
            segment.long_repeat_due = None
            self._complete_segment_locked(reason)

    def _complete_segment_locked(self, reason: str) -> None:
        segment = self._segment
        if segment is None or segment.completed:
            return
        segment.completed = True
        self._queue_expression_locked(
            self._default_expression_locked(),
            segment.event_id,
            segment.turn_id,
            segment.segment_id,
        )
        self._queue_command_locked(
            "segment_completed",
            {
                "cause_event_id": segment.event_id,
                "token": segment.token,
                "turn_id": segment.turn_id,
                "segment_id": segment.segment_id,
                "reason": reason,
            },
        )
        self._segment = None
        if self._state != "exiting" and self._state != "switching":
            self._state = "thinking" if self._thinking else "idle"
        now = self._clock()
        self._idle_recover_due = now + self._config.idle_recover_delay

    def _cancel_segment_locked(self, reason: str, cause_event_id: str) -> None:
        segment = self._segment
        if self._audio is not None:
            audio = self._audio
            self._queue_command_locked(
                "mouth_amplitude",
                {
                    "token": audio.token,
                    "turn_id": audio.turn_id,
                    "segment_id": audio.segment_id,
                    "amplitude": 0.0,
                    "target_renderer_ids": sorted(self._renderers),
                },
            )
            self._queue_command_locked(
                "stop_audio",
                {
                    "cause_event_id": cause_event_id,
                    "token": audio.token,
                    "turn_id": audio.turn_id,
                    "segment_id": audio.segment_id,
                    "reason": reason,
                    "target_renderer_ids": sorted(audio.expected_renderers),
                },
            )
            self._audio = None
        if segment is not None and not segment.completed:
            self._queue_expression_locked(
                self._default_expression_locked(),
                cause_event_id,
                segment.turn_id,
                segment.segment_id,
            )
            self._queue_command_locked(
                "segment_cancelled",
                {
                    "cause_event_id": cause_event_id,
                    "token": segment.token,
                    "turn_id": segment.turn_id,
                    "segment_id": segment.segment_id,
                    "reason": reason,
                },
            )
        self._segment = None

    def _cancel_motion_locked(self, reason: str, cause_event_id: str) -> None:
        motion = self._motion
        if motion is None:
            return
        self._queue_command_locked(
            "stop_motion",
            {
                "cause_event_id": cause_event_id,
                "token": motion.token,
                "reason": reason,
                "target_renderer_ids": sorted(motion.expected_renderers),
            },
        )
        self._motion = None

    # ------------------------------------------------------------------
    # Protocol and diagnostics helpers

    def _queue_command_locked(self, command_type: str, data: Dict[str, Any]) -> str:
        self._seq += 1
        event_id = self._new_event_id()
        self._outbox.append(
            {
                "v": PROTOCOL_VERSION,
                "type": command_type,
                "event_id": event_id,
                "session_id": self._session_id,
                "source": "live2d_controller",
                "timestamp": self._wall_clock(),
                "seq": self._seq,
                "data": data,
            }
        )
        return event_id

    def _flush(self) -> None:
        with self._lock:
            if self._flushing:
                return
            self._flushing = True
        try:
            while True:
                with self._lock:
                    if not self._outbox:
                        return
                    command = self._outbox.popleft()
                self._emit_callback(command)
        finally:
            with self._lock:
                self._flushing = False

    def _queue_close_renderer_locked(self, cause_event_id: str, reason: str) -> None:
        self._queue_command_locked(
            "close_renderer",
            {
                "cause_event_id": cause_event_id,
                "reason": reason,
                "target_renderer_ids": sorted(self._renderers),
            },
        )

    def _effective_catalog_locked(self) -> Dict[str, int]:
        if not self._renderers:
            return dict(self._base_catalog)
        catalogs = [info["motion_groups"] for info in self._renderers.values()]
        if any(not catalog for catalog in catalogs):
            return {}
        common = set(catalogs[0])
        for catalog in catalogs[1:]:
            common.intersection_update(catalog)
        return {group: min(catalog[group] for catalog in catalogs) for group in common}

    def _effective_expression_ids_locked(self) -> Set[str]:
        if not self._renderers:
            return set()
        catalogs = [set(info.get("expression_ids", ())) for info in self._renderers.values()]
        if any(not catalog for catalog in catalogs):
            return set()
        common = set(catalogs[0])
        for catalog in catalogs[1:]:
            common.intersection_update(catalog)
        return common

    def _default_expression_locked(self) -> str:
        supported = self._effective_expression_ids_locked()
        return select_expression_for_motion("IDLE", None, supported) or ""

    def _expression_for_motion_locked(self, group: str, index: int) -> str:
        supported = self._effective_expression_ids_locked()
        motion_file = self._motion_files_by_group.get(group, ())
        selected_file = motion_file[index] if 0 <= index < len(motion_file) else None
        return select_expression_for_motion(group, selected_file, supported) or ""

    @staticmethod
    def _read_motion_files_locked(model: Mapping[str, Any]) -> Dict[str, tuple[str, ...]]:
        model_json_path = str(model.get("model_json_path") or "")
        if not model_json_path:
            return {}
        try:
            return motion_files_by_group_from_model_json(model_json_path)
        except (OSError, TypeError, ValueError):
            return {}

    def _queue_expression_locked(
        self,
        expression: str,
        cause_event_id: str,
        turn_id: str,
        segment_id: str,
        *,
        target_renderer_ids: Optional[list[str]] = None,
    ) -> None:
        expression = str(expression or "")
        self._current_expression = expression
        if not self._renderers:
            return
        self._queue_command_locked(
            "set_expression",
            {
                "cause_event_id": cause_event_id,
                "turn_id": turn_id,
                "segment_id": segment_id,
                "expression": expression,
                "target_renderer_ids": target_renderer_ids
                if target_renderer_ids is not None
                else sorted(self._renderers),
            },
        )

    @staticmethod
    def _normalize_expression_ids(value: Any) -> Set[str]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            return set()
        return {str(expression) for expression in value if str(expression)}

    @staticmethod
    def _normalize_catalog(value: Any) -> Dict[str, int]:
        if not isinstance(value, Mapping):
            return {}
        result: Dict[str, int] = {}
        for raw_group, raw_count in value.items():
            group = str(raw_group)
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if group and count > 0:
                result[group] = count
        return result

    def _matching_motion_locked(self, data: Dict[str, Any]) -> Optional[_Motion]:
        motion = self._motion
        if motion is None or str(data.get("token") or "") != motion.token:
            return None
        if data.get("turn_id") is not None and str(data.get("turn_id")) != motion.turn_id:
            return None
        if data.get("segment_id") is not None and str(data.get("segment_id")) != motion.segment_id:
            return None
        return motion

    def _matching_audio_locked(self, data: Dict[str, Any]) -> Optional[_Audio]:
        audio = self._audio
        if audio is None or str(data.get("token") or "") != audio.token:
            return None
        if data.get("turn_id") is not None and str(data.get("turn_id")) != audio.turn_id:
            return None
        if data.get("segment_id") is not None and str(data.get("segment_id")) != audio.segment_id:
            return None
        return audio

    @staticmethod
    def _renderer_expected(renderer_id: str, expected: Set[str]) -> bool:
        return not expected or renderer_id in expected

    @staticmethod
    def _facts_complete(expected: Set[str], actual: Set[str]) -> bool:
        # With no registered renderer, one unscoped/default fact is sufficient.
        return bool(actual) if not expected else expected.issubset(actual)

    def _remember_event_locked(self, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        while len(self._seen_event_order) > self._seen_event_limit:
            old = self._seen_event_order.popleft()
            self._seen_event_ids.discard(old)
        return True

    def _can_idle_locked(self) -> bool:
        return (
            self._state == "idle"
            and not self._thinking
            and self._segment is None
            and self._audio is None
            and self._motion is None
        )

    def _new_event_id(self) -> str:
        return str(self._id_factory())

    def _new_token_locked(self, kind: str) -> str:
        self._token_seq += 1
        return "%s:%s:%d" % (kind, self._session_id, self._token_seq)

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError("Live2DBehaviorController is closed")

    def _motion_snapshot_locked(self) -> Optional[Dict[str, Any]]:
        motion = self._motion
        if motion is None:
            return None
        return {
            "token": motion.token,
            "event_id": motion.command_event_id,
            "cause_event_id": motion.cause_event_id,
            "group": motion.group,
            "index": motion.index,
            "priority": motion.priority,
            "purpose": motion.purpose,
            "turn_id": motion.turn_id,
            "segment_id": motion.segment_id,
            "expected_renderers": sorted(motion.expected_renderers),
            "started_renderers": sorted(motion.started_renderers),
            "finished_renderers": sorted(motion.finished_renderers),
        }

    def _audio_snapshot_locked(self) -> Optional[Dict[str, Any]]:
        audio = self._audio
        if audio is None:
            return None
        return {
            "token": audio.token,
            "event_id": audio.command_event_id,
            "cause_event_id": audio.cause_event_id,
            "url": audio.url,
            "turn_id": audio.turn_id,
            "segment_id": audio.segment_id,
            "expected_renderers": sorted(audio.expected_renderers),
            "started_renderers": sorted(audio.started_renderers),
            "ended_renderers": sorted(audio.ended_renderers),
        }

    def _segment_snapshot_locked(self) -> Optional[Dict[str, Any]]:
        segment = self._segment
        if segment is None:
            return None
        return {
            "event_id": segment.event_id,
            "token": segment.token,
            "turn_id": segment.turn_id,
            "segment_id": segment.segment_id,
            "motion_group": segment.motion_group,
            "audio_url": segment.audio_url,
            "audio_duration": segment.audio_duration,
            "audio_token": segment.audio_token,
            "first_motion_finished": segment.first_motion_finished,
            "long_repeat_count": segment.long_repeat_count,
            "long_repeat_due": segment.long_repeat_due,
            "completed": segment.completed,
        }


__all__ = [
    "BehaviorConfig",
    "Live2DBehaviorController",
    "PROTOCOL_VERSION",
]
