"""Shared behavior host for non-Pygame renderers such as Electron."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from collections import deque
from copy import deepcopy
from queue import Empty
from threading import Event
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
        self._bye_requested = False
        self._scheduled_tokens: dict[str, str] = {}
        self._renderer_is_sakiko = False
        self._renderer_ids: set[str] = set()
        self._ready = False
        self._sakiko_conversion = owner.sakiko_conversion
        self._pending_conversion: SakikoConversionDecision | None = None
        self._pending_conversion_model_token = ""
        self._pending_conversion_renderers: set[str] = set()
        self._pending_conversion_switch: dict[str, Any] | None = None
        # The owner may finish a conversion before a renderer connects. Keep
        # the already-resolved commands so that a late renderer executes the
        # same result without causing another business decision.
        self._conversion_replay_switch: dict[str, Any] | None = None
        self._conversion_replay_motion: dict[str, Any] | None = None
        self._conversion_replay_renderers: set[str] = set()
        self._model_urls: dict[str, str] = {}
        self._model_urls_by_renderer: dict[str, dict[str, str]] = {}
        self._renderer_roles: dict[str, str] = {}
        self._renderer_model_keys: dict[str, str] = {}
        self._renderer_instances: dict[str, str] = {}
        self._renderer_catalogs: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {}

    def start_bye(self) -> bool:
        if self._bye_requested:
            return False
        self._bye_requested = True
        command = self._behavior.start_named_motion(turn_id="", segment_id="", group="bye", priority=3)
        if command is None:
            self._emit({"type": "close_renderer", "data": {"reason": "bye_motion_unavailable"}})
            return False
        self._bye_token = command.command_id
        motion = motion_command(command)
        assert motion is not None
        if self._renderer_ids:
            motion.setdefault("data", {})["target_renderer_ids"] = sorted(self._renderer_ids)
        self._emit(motion)
        return True

    @property
    def ready(self) -> bool:
        return self._ready

    def set_thinking(self, active: bool) -> bool:
        """Accept an upstream fact; only the shared scheduler owns its timer."""
        self._scheduler.set_thinking(active)
        self._emit({"type": "thinking_changed", "data": {"active": active}})
        return True

    def handle_runtime_control(self, data: Mapping[str, Any]) -> bool:
        """Route legacy UI controls through the owner, preserving mechanics-only renderers."""
        command_type = str(data.get("type") or "")
        if command_type == "start_talking":
            return self._emit_scheduled(self._scheduler.request_motion("talking_motion", 4, "talking"))
        if command_type == "stop_talking":
            self._emit({"type": "stop_motion", "data": {}})
            return True
        if command_type == "cancel_turn":
            self._behavior.cancel()
            self._emit({"type": "stop_audio", "data": {}})
            self._emit({"type": "stop_motion", "data": {}})
            self._emit({"type": "reset", "data": {}})
            return True
        if command_type == "exit":
            return self.start_bye()
        if command_type in {"change_l2d_background", "switch_l2d_fps", "toggle_l2d_layout_edit"}:
            self._emit({"type": command_type, "data": dict(data)})
            return True
        if command_type == "switch_live2d":
            payload = dict(data)
            payload.pop("type", None)
            model_path = str(payload.get("model_json") or payload.get("model_url") or "")
            if model_path and "electron_model_url" not in payload:
                marker = "live2d_related"
                if marker in model_path.replace("\\", "/"):
                    relative = model_path.replace("\\", "/").split(marker, 1)[1].lstrip("/")
                    payload["electron_model_url"] = f"http://127.0.0.1:9877/model/{relative}"
            self._emit({"type": "switch_live2d", "data": payload})
            return True
        return False

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
            command_payload = command
            if self._renderer_ids:
                command_payload.setdefault("data", {})["target_renderer_ids"] = sorted(self._renderer_ids)
            self._emit(command_payload)
        return True

    def handle_renderer_fact(self, message: Mapping[str, Any]) -> bool:
        data = message.get("data")
        if not isinstance(data, Mapping):
            return False
        if message.get("type") == "renderer_ready":
            renderer_id = str(data.get("renderer_id") or "")
            renderer_instance_id = str(data.get("renderer_instance_id") or renderer_id or "anonymous")
            if renderer_id:
                self._renderer_ids.add(renderer_id)
                self._renderer_roles[renderer_id] = str(data.get("renderer_role") or "")
                self._renderer_instances[renderer_id] = renderer_instance_id
            self._ready = True
            motion_files = data.get("motion_files_by_group")
            expression_ids = data.get("expression_ids", ())
            normalized_expression_ids = tuple(expression_ids) if isinstance(expression_ids, (list, tuple)) else ()
            if renderer_id:
                if isinstance(motion_files, Mapping):
                    self._renderer_catalogs[renderer_id] = ("files", dict(motion_files), normalized_expression_ids)
                else:
                    groups = data.get("motion_groups", {})
                    self._renderer_catalogs[renderer_id] = ("groups", dict(groups) if isinstance(groups, Mapping) else {}, ())
                self._apply_canonical_catalog()
            elif isinstance(motion_files, Mapping):
                self._behavior.set_model_catalog(motion_files, normalized_expression_ids)
                self._scheduler.set_model_catalog(motion_files, normalized_expression_ids)
            else:
                self._behavior.set_capabilities(data.get("motion_groups", {}))
                self._scheduler.set_catalog(data.get("motion_groups", {}))
            model_key = str(data.get("model_key", ""))
            self._renderer_is_sakiko = model_key.lower() == "sakiko"
            if renderer_id:
                self._renderer_model_keys[renderer_id] = model_key
            urls = data.get("model_urls")
            if isinstance(urls, Mapping):
                normalized_urls = {str(key): str(value) for key, value in urls.items() if value}
                self._model_urls_by_renderer[renderer_id] = normalized_urls
                self._model_urls = normalized_urls
            if self._pending_conversion is not None and renderer_id:
                expected_token = self._pending_conversion_model_token
                actual_token = str(data.get("model_token") or "")
                if expected_token and actual_token != expected_token and self._pending_conversion_renderers:
                    self._pending_conversion_renderers.add(renderer_id)
                    switch = dict(self._pending_conversion_switch or {})
                    switch["target_renderer_ids"] = [renderer_id]
                    self._emit({"type": "switch_live2d", "data": switch})
                else:
                    self._pending_conversion_renderers.discard(renderer_id)
                if not self._pending_conversion_renderers:
                    pending, self._pending_conversion = self._pending_conversion, None
                    self._pending_conversion_model_token = ""
                    switch = dict(self._pending_conversion_switch or {})
                    self._pending_conversion_switch = None
                    self._conversion_replay_switch = switch or None
                    self._conversion_replay_renderers = {
                        self._renderer_instance_key(current_id)
                        for current_id in self._renderer_ids
                    }
                    self._emit_conversion_motion(pending)
            elif renderer_id and self._conversion_replay_switch is not None:
                # A renderer that joins after the conversion barrier must be
                # brought to the owner's current model and receive the exact
                # motion command already sent to the other renderers.
                expected_token = str(self._conversion_replay_switch.get("model_token") or "")
                actual_token = str(data.get("model_token") or "")
                renderer_instance_key = self._renderer_instance_key(renderer_id)
                if actual_token != expected_token:
                    switch = deepcopy(self._conversion_replay_switch)
                    switch["target_renderer_ids"] = [renderer_id]
                    self._emit({"type": "switch_live2d", "data": switch})
                elif renderer_instance_key not in self._conversion_replay_renderers and self._conversion_replay_motion is not None:
                    motion = deepcopy(self._conversion_replay_motion)
                    motion.setdefault("data", {})["target_renderer_ids"] = [renderer_id]
                    self._conversion_replay_renderers.add(renderer_instance_key)
                    self._emit(motion)
            return True
        fact_renderer_id = str(data.get("renderer_id") or "")
        if self._renderer_ids and fact_renderer_id and fact_renderer_id not in self._renderer_ids:
            return False
        if message.get("type") == "renderer_intent" and data.get("intent") == "click":
            command = self._scheduler.click(is_sakiko=self._canonical_renderer_is_sakiko())
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
        pygame_urls = next(
            (urls for renderer_id, urls in self._model_urls_by_renderer.items()
             if self._renderer_roles.get(renderer_id) == "pygame"),
            {},
        )
        candidates = model_urls if isinstance(model_urls, Mapping) and model_urls else (pygame_urls or self._model_urls)
        model_url = str(candidates.get(decision.model_target, ""))
        if not model_url:
            return False
        self._conversion_replay_switch = None
        self._conversion_replay_motion = None
        self._conversion_replay_renderers.clear()
        self._pending_conversion = decision
        self._pending_conversion_model_token = uuid4().hex
        self._pending_conversion_renderers = set(self._renderer_ids)
        electron_urls = next(
            (urls for renderer_id, urls in self._model_urls_by_renderer.items()
             if self._renderer_roles.get(renderer_id) == "electron"),
            {},
        )
        self._pending_conversion_switch = {
            "model_url": model_url,
            "electron_model_url": electron_urls.get(decision.model_target, model_url),
            "character_folder": "sakiko",
            "character_folder_name": "sakiko",
            "model_token": self._pending_conversion_model_token,
        }
        self._emit({"type": "switch_live2d", "data": dict(self._pending_conversion_switch)})
        return True

    def _emit_conversion_motion(self, decision: SakikoConversionDecision) -> bool:
        expression = self._scheduler.resolve_semantic_expression(decision.semantic_expression) if decision.semantic_expression else None
        if decision.fixed_index is None:
            command = self._scheduler.request_motion(decision.motion_group, decision.priority, decision.purpose)
        else:
            command = self._scheduler.request_fixed_motion(decision.motion_group, decision.fixed_index, decision.priority, decision.purpose)
        if command is not None and expression is not None:
            command = ScheduledMotion(command.group, command.index, command.priority, command.purpose, expression)
        return self._emit_scheduled(command, replay_for_late_renderers=True)

    def tick(self) -> bool:
        return self._emit_scheduled(self._scheduler.tick())

    def _emit_scheduled(self, scheduled: ScheduledMotion | None, *, replay_for_late_renderers: bool = False) -> bool:
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
        if self._renderer_ids:
            motion.setdefault("data", {})["target_renderer_ids"] = sorted(self._renderer_ids)
        if replay_for_late_renderers:
            self._conversion_replay_motion = deepcopy(motion)
        self._emit(motion)
        return True

    def _emit_audio(self, command: StartAudio | None, segment) -> None:
        if isinstance(command, StartAudio) and segment is not None:
            payload = audio_command(command, segment)
            if self._renderer_ids:
                payload.setdefault("data", {})["target_renderer_ids"] = sorted(self._renderer_ids)
                audio_owner = self._audio_owner_renderer_id()
                if audio_owner:
                    payload.setdefault("data", {})["target_renderer_id"] = audio_owner
            self._emit(payload)

    def _audio_owner_renderer_id(self) -> str | None:
        """Select one runtime for audible playback while motions fan out."""
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in self._renderer_ids
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return candidates[0]
        return None

    def _canonical_renderer_is_sakiko(self) -> bool:
        """Use one stable runtime role when multiple renderer facts disagree."""
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in self._renderer_ids
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return self._renderer_model_keys.get(candidates[0], "").lower() == "sakiko"
        return self._renderer_is_sakiko

    def _canonical_renderer_id(self) -> str | None:
        for role in ("pygame", "electron"):
            candidates = sorted(
                renderer_id for renderer_id in self._renderer_ids
                if self._renderer_roles.get(renderer_id) == role
            )
            if candidates:
                return candidates[0]
        return sorted(self._renderer_ids)[0] if self._renderer_ids else None

    def _apply_canonical_catalog(self) -> None:
        renderer_id = self._canonical_renderer_id()
        if renderer_id is None:
            return
        catalog = self._renderer_catalogs.get(renderer_id)
        if catalog is None:
            return
        kind, values, expression_ids = catalog
        if kind == "files":
            self._behavior.set_model_catalog(values, expression_ids)
            self._scheduler.set_model_catalog(values, expression_ids)
        else:
            self._behavior.set_capabilities(values)
            self._scheduler.set_catalog(values)

    def _renderer_instance_key(self, renderer_id: str) -> str:
        return f"{renderer_id}:{self._renderer_instances.get(renderer_id, renderer_id)}"


class SharedRendererService:
    """Queue adapter for bridge deployments; its policy remains in the host."""

    def __init__(self, intent_queue, renderer_fact_queue, command_queue, owner: AuthoritativeLive2DOwner) -> None:
        self._intents = intent_queue
        self._facts = renderer_fact_queue
        self._host = SharedRendererHost(command_queue.put, owner)
        self._pending_intents = deque()
        self._bye_handled = Event()

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
            self._pending_intents.append(intent)
        while self._pending_intents:
            intent = self._pending_intents[0]
            if not isinstance(intent, Mapping):
                self._pending_intents.popleft()
                continue
            intent_type = intent.get("type")
            # Emotion segments require a renderer catalog; lifecycle/control
            # intents must still be consumed when model loading failed.
            if intent_type == "emotion_segment" and not self._host.ready:
                control_index = next(
                    (index for index, candidate in enumerate(self._pending_intents)
                     if isinstance(candidate, Mapping) and candidate.get("type") != "emotion_segment"),
                    None,
                )
                if control_index is None:
                    break
                intent = self._pending_intents[control_index]
                del self._pending_intents[control_index]
                intent_type = intent.get("type")
            else:
                self._pending_intents.popleft()
            if intent_type != "emotion_segment":
                if intent_type == "bye":
                    handled += int(self._host.start_bye())
                    self._bye_handled.set()
                elif intent_type == "thinking_changed":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.set_thinking(data.get("active") is True))
                elif intent_type == "sakiko_conversion":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.start_sakiko_conversion(data.get("value"), data.get("model_urls", {})))
                elif intent_type == "runtime_control":
                    data = intent.get("data", {})
                    handled += int(isinstance(data, Mapping) and self._host.handle_runtime_control(data))
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

    def wait_for_bye(self, timeout_seconds: float = 2.0) -> bool:
        """Wait until the queued bye intent has become an exact runtime command."""
        return self._bye_handled.wait(max(0.0, float(timeout_seconds)))

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        """Run until the caller's lifecycle owner requests a clean stop."""
        drain_deadline = None
        while True:
            handled = self.run_once()
            if stop_event.is_set():
                if self._bye_handled.is_set():
                    break
                if drain_deadline is None:
                    drain_deadline = time.monotonic() + 2.0
                # A shutdown intent may have been queued immediately before
                # the stop event. Give the owner a bounded opportunity to
                # turn it into an exact close/bye command.
                if not self._pending_intents and (self._intents.empty() or not self._host.ready):
                    break
                if time.monotonic() >= drain_deadline:
                    break
            if handled == 0:
                time.sleep(poll_interval_seconds)
