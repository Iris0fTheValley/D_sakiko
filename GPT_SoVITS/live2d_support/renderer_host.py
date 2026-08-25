"""Shared behavior host for non-Pygame renderers such as Electron."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from queue import Empty
import time
from uuid import uuid4
from typing import Any

from live2d_support.renderer_contract import audio_command, motion_command, normalize_renderer_fact
from live2d_support.shared_behavior import ExactMotion, PlaySegment, StartAudio
from live2d_support.behavior_scheduler import ScheduledMotion
from live2d_support.sakiko_conversion import SakikoConversionDecision, SharedSakikoConversion
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner


CommandEmitter = Callable[[dict[str, Any]], None]


class SharedRendererHost:
    """Adapt shared behavior to a command/fact transport without SDK imports."""

    def __init__(self, emit: CommandEmitter, owner: AuthoritativeLive2DOwner) -> None:
        self._emit = emit
        self._behavior = owner.behavior
        self._scheduler = owner.scheduler
        self._bye_token = ""
        self._scheduled_tokens: dict[str, str] = {}
        self._renderer_is_sakiko = False
        self._renderer_id = ""
        self._sakiko_conversion = owner.sakiko_conversion
        self._pending_conversion: SakikoConversionDecision | None = None

    def start_bye(self) -> bool:
        command = self._behavior.start_named_motion(turn_id="", segment_id="", group="bye", priority=3)
        if command is None:
            self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_unavailable"}})
            return False
        self._bye_token = command.command_id
        motion = motion_command(command)
        assert motion is not None
        self._emit(motion)
        return True

    def set_thinking(self, active: bool) -> bool:
        """Accept an upstream fact; only the shared scheduler owns its timer."""
        self._scheduler.set_thinking(active)
        self._emit({"type": "thinking_changed", "data": {"active": active}})
        return True

    def start_emotion_segment(self, *, turn_id: str, segment_id: str, emotion: str, audio_path: str, audio_duration_seconds: float = 0.0) -> bool:
        segment = self._behavior.start_emotion_segment(
            turn_id=turn_id, segment_id=segment_id, emotion=emotion, audio_path=audio_path, audio_duration_seconds=audio_duration_seconds,
        )
        if segment is None:
            return False
        command = motion_command(segment)
        if command is None:
            self._emit_audio(self._behavior.motion_rejected(segment.command_id), segment)
        else:
            self._scheduler.start_segment(segment.motion.group, segment.audio_duration_seconds)
            self._emit(command)
        return True

    def handle_renderer_fact(self, message: Mapping[str, Any]) -> bool:
        data = message.get("data")
        if not isinstance(data, Mapping):
            return False
        if message.get("type") == "renderer_ready":
            renderer_id = str(data.get("renderer_id") or "")
            # A reconnect is a capability refresh, not an audio/motion-idle
            # fact.  In particular, it must not silently complete an active
            # segment.  A host instance accepts one selected renderer only;
            # facts from another renderer are stale unless a later lifecycle
            # owner creates a new host for that renderer session.
            if self._renderer_id and renderer_id and renderer_id != self._renderer_id:
                return False
            if renderer_id:
                self._renderer_id = renderer_id
            motion_files = data.get("motion_files_by_group")
            expression_ids = data.get("expression_ids", ())
            if isinstance(motion_files, Mapping):
                self._behavior.set_model_catalog(motion_files, expression_ids if isinstance(expression_ids, (list, tuple)) else ())
                self._scheduler.set_model_catalog(motion_files, expression_ids if isinstance(expression_ids, (list, tuple)) else ())
            else:
                self._behavior.set_capabilities(data.get("motion_groups", {}))
                self._scheduler.set_catalog(data.get("motion_groups", {}))
            self._renderer_is_sakiko = str(data.get("model_key", "")).lower() == "sakiko"
            if self._pending_conversion is not None:
                pending, self._pending_conversion = self._pending_conversion, None
                self._emit_conversion_motion(pending)
            return True
        fact_renderer_id = str(data.get("renderer_id") or "")
        if self._renderer_id and fact_renderer_id and fact_renderer_id != self._renderer_id:
            return False
        if message.get("type") == "renderer_intent" and data.get("intent") == "click":
            command = self._scheduler.click(is_sakiko=self._renderer_is_sakiko)
            return self._emit_scheduled(command)
        normalized = normalize_renderer_fact(message)
        if normalized is None:
            return False
        fact, token = normalized
        scheduled_purpose = self._scheduled_tokens.get(token)
        if scheduled_purpose is not None:
            if fact == "motion_started":
                self._scheduler.motion_started(scheduled_purpose)
                return True
            if fact in {"motion_rejected", "motion_finished", "command_failed"}:
                self._scheduled_tokens.pop(token, None)
                self._scheduler.motion_finished(scheduled_purpose)
                return True
        active = self._behavior.active_command
        if fact == "motion_started":
            if active is not None and token == active.command_id:
                self._scheduler.motion_started("emotion")
            self._emit_audio(self._behavior.motion_started(token), active)
            return True
        if fact == "motion_rejected":
            if active is not None and token == active.command_id:
                self._scheduler.motion_finished("emotion")
            self._emit_audio(self._behavior.motion_rejected(token), active)
            return True
        if fact == "motion_finished":
            if active is not None and token == active.command_id:
                self._scheduler.motion_finished("emotion")
            handled = self._behavior.motion_finished(token)
            if handled and token == self._bye_token:
                self._bye_token = ""
                self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_finished"}})
            return handled
        if fact == "audio_started":
            handled = self._behavior.audio_started(token)
            if handled:
                self._scheduler.set_audio_busy(True)
            return handled
        if fact == "audio_ended":
            handled = self._behavior.audio_ended(token)
            if handled:
                self._scheduler.set_audio_busy(False)
            return handled
        if fact == "command_failed":
            self._emit_audio(
                self._behavior.command_failed(token, str(data.get("phase") or "unknown")),
                active,
            )
            return True
        return False

    def start_sakiko_conversion(self, conversion, model_urls: Mapping[str, str]) -> bool:
        """Decide once; the renderer only reloads the requested model."""
        decision = self._sakiko_conversion.decide(conversion)
        if decision.model_target == "current":
            return self._emit_conversion_motion(decision)
        model_url = str(model_urls.get(decision.model_target, ""))
        if not model_url:
            return False
        self._pending_conversion = decision
        self._emit({"type": "switch_live2d", "data": {"model_url": model_url, "character_folder": "sakiko"}})
        return True

    def _emit_conversion_motion(self, decision: SakikoConversionDecision) -> bool:
        expression = self._scheduler.resolve_semantic_expression(decision.semantic_expression) if decision.semantic_expression else None
        if decision.fixed_index is None:
            command = self._scheduler.request_motion(decision.motion_group, decision.priority, decision.purpose)
        else:
            command = self._scheduler.request_fixed_motion(decision.motion_group, decision.fixed_index, decision.priority, decision.purpose)
        if command is not None and expression is not None:
            command = ScheduledMotion(command.group, command.index, command.priority, command.purpose, expression)
        return self._emit_scheduled(command)

    def tick(self) -> bool:
        return self._emit_scheduled(self._scheduler.tick())

    def _emit_scheduled(self, scheduled: ScheduledMotion | None) -> bool:
        if scheduled is None:
            return False
        self._scheduler.motion_requested(scheduled.purpose)
        token = uuid4().hex
        command = PlaySegment(token, "scheduler", scheduled.purpose, ExactMotion(
            scheduled.group, scheduled.index, scheduled.priority, expression_id=scheduled.expression_id,
        ), "", 0.0)
        self._scheduled_tokens[token] = scheduled.purpose
        motion = motion_command(command)
        assert motion is not None
        self._emit(motion)
        return True

    def _emit_audio(self, command: StartAudio | None, segment) -> None:
        if isinstance(command, StartAudio) and segment is not None:
            self._emit(audio_command(command, segment))


class SharedRendererService:
    """Queue adapter for bridge deployments; its policy remains in the host."""

    def __init__(self, intent_queue, renderer_fact_queue, command_queue, owner: AuthoritativeLive2DOwner) -> None:
        self._intents = intent_queue
        self._facts = renderer_fact_queue
        self._host = SharedRendererHost(command_queue.put, owner)

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
                if isinstance(intent, Mapping) and intent.get("type") == "bye":
                    handled += int(self._host.start_bye())
                elif isinstance(intent, Mapping) and intent.get("type") == "thinking_changed":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.set_thinking(data.get("active") is True))
                elif isinstance(intent, Mapping) and intent.get("type") == "sakiko_conversion":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.start_sakiko_conversion(data.get("value"), data.get("model_urls", {})))
                continue
            data = intent.get("data", {})
            if not isinstance(data, Mapping):
                continue
            handled += int(self._host.start_emotion_segment(
                turn_id=str(data.get("turn_id", "")), segment_id=str(data.get("segment_id", "")),
                emotion=str(data.get("emotion", "")), audio_path=str(data.get("audio_path", "")),
                audio_duration_seconds=float(data.get("audio_duration_seconds", 0.0) or 0.0),
            ))
        handled += int(self._host.tick())
        return handled

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        """Run until the caller's lifecycle owner requests a clean stop."""
        while not stop_event.is_set():
            if self.run_once() == 0:
                time.sleep(poll_interval_seconds)
