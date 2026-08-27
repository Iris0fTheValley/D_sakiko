from __future__ import annotations

import os
import sys
import unittest
from queue import Queue
from random import Random


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from live2d_support.authoritative_owner import AuthoritativeLive2DOwner
from live2d_support.renderer_host import SharedRendererService
from live2d_support.runtime_ingress import TheaterIngressAdapter
from live2d_support.theater_runtime import _TheaterTurnEventSink


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TheaterAuthoritativeRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.intents = Queue()
        self.facts = Queue()
        self.commands = Queue()
        self.turns = Queue()
        self.owner = AuthoritativeLive2DOwner(clock=self.clock, rng=Random(7))
        self.service = SharedRendererService(
            self.intents,
            self.facts,
            self.commands,
            self.owner,
            theater_event_queue=self.turns,
        )
        self.facts.put(self._ready_fact(model_token="initial"))
        self.intents.put({
            "type": "theater_active_slots",
            "data": {
                "type": "set_active_slots",
                "slots": [
                    {"slot": 0, "character_name": "A", "character_folder_name": "a", "model_json_path": "a.model3.json"},
                    {"slot": 1, "character_name": "B", "character_folder_name": "b", "model_json_path": "b.model3.json"},
                ],
                "motion_facing_mode": "face_to_face",
            },
        })
        self.service.run_once()
        switch = self.commands.get_nowait()
        self.assertEqual(switch["type"], "switch_live2d")
        self._finish_model_switch(switch, "a")

    @staticmethod
    def _ready_fact(*, model_token: str, model_key: str = "a") -> dict:
        return {
            "type": "renderer_ready",
            "data": {
                "renderer_id": "pygame-renderer",
                "renderer_instance_id": "instance-1",
                "renderer_role": "pygame",
                "model_token": model_token,
                "model_key": model_key,
                "motion_files_by_group": {
                    "change_character": ["switch.motion3.json"],
                    "happiness_L": ["happy-left.motion3.json"],
                    "happiness_R": ["happy-right.motion3.json"],
                },
                "capabilities": {"motion": True, "audio": True, "lipsync": True},
            },
        }

    def _finish_model_switch(self, switch: dict, model_key: str) -> None:
        token = switch["data"]["model_token"]
        self.facts.put(self._ready_fact(model_token=token, model_key=model_key))
        self.service.run_once()
        if switch["data"].get("initial_model"):
            return
        transition = self.commands.get_nowait()
        self.assertEqual(transition["type"], "play_motion")
        transition_token = transition["data"]["token"]
        self.facts.put({"type": "motion_started", "data": {"renderer_id": "pygame-renderer", "token": transition_token}})
        self.facts.put({"type": "motion_finished", "data": {"renderer_id": "pygame-renderer", "token": transition_token}})
        self.service.run_once()

    def test_playlist_switches_models_and_advances_only_from_runtime_facts(self):
        self.intents.put({
            "type": "theater_playlist",
            "data": {
                "playlist": [
                    {"turn_uid": "turn-a", "character_name": "A", "text": "first", "emotion": "LABEL_0", "audio_path": "a.wav", "audio_duration_seconds": 1.0},
                    {"turn_uid": "turn-b", "character_name": "B", "text": "second", "emotion": "LABEL_0", "audio_path": "b.wav", "audio_duration_seconds": 1.0},
                ],
                "preserve_playback": False,
            },
        })
        self.service.run_once()

        first_turn = self.turns.get_nowait()
        first_motion = self.commands.get_nowait()
        self.assertEqual(first_turn["turn_uid"], "turn-a")
        self.assertEqual(first_motion["type"], "play_motion")
        self.assertEqual(first_motion["data"]["position"], "L")
        first_token = first_motion["data"]["token"]

        self.facts.put({"type": "motion_started", "data": {"renderer_id": "pygame-renderer", "token": first_token}})
        self.service.run_once()
        first_audio = self.commands.get_nowait()
        self.assertEqual(first_audio["type"], "play_audio")
        self.assertTrue(self.turns.empty())

        self.facts.put({"type": "audio_started", "data": {"renderer_id": "pygame-renderer", "token": first_token}})
        self.facts.put({"type": "audio_ended", "data": {"renderer_id": "pygame-renderer", "token": first_token}})
        self.service.run_once()
        self.assertTrue(self.commands.empty())

        self.clock.now += 0.5
        self.service.run_once()
        second_turn = self.turns.get_nowait()
        second_motion = self.commands.get_nowait()
        self.assertEqual(second_turn["turn_uid"], "turn-b")
        self.assertEqual(second_motion["type"], "play_motion")
        self.assertEqual(second_motion["data"]["position"], "R")
        self.assertEqual(second_motion["data"]["target_slot"], 1)

    def test_ingress_translates_legacy_queues_without_choosing_behavior(self):
        controls, playlists, intents = Queue(), Queue(), Queue()
        ingress = TheaterIngressAdapter(controls, playlists, intents)
        controls.put({"type": "set_active_slots", "slots": []})
        playlists.put({"playlist": [{"character_name": "A", "emotion": "LABEL_0", "audio_path": "missing.wav"}]})
        self.assertEqual(ingress.run_once(), 2)
        self.assertEqual(intents.get_nowait()["type"], "theater_active_slots")
        playlist_intent = intents.get_nowait()
        self.assertEqual(playlist_intent["type"], "theater_playlist")
        self.assertEqual(playlist_intent["data"]["playlist"][0]["audio_duration_seconds"], 0.0)

    def test_turn_sink_preserves_speaker_and_translation_for_pygame_overlay(self):
        overlays, turns = Queue(), Queue()
        sink = _TheaterTurnEventSink(overlays, turns)
        turn = {"character_name": "A", "text": "line", "translation": "译文"}
        sink.put(turn)
        self.assertEqual(overlays.get_nowait(), {"character_name": "A", "text": "line\n译文"})
        self.assertEqual(turns.get_nowait(), turn)

    def test_slot_change_waits_for_active_turn_then_uses_owner_switch(self):
        self.intents.put({
            "type": "theater_playlist",
            "data": {"playlist": [{
                "turn_uid": "active", "character_name": "A", "text": "line",
                "emotion": "LABEL_0", "audio_path": "a.wav", "audio_duration_seconds": 1.0,
            }]},
        })
        self.service.run_once()
        active_motion = self.commands.get_nowait()
        active_token = active_motion["data"]["token"]

        self.intents.put({
            "type": "theater_active_slots",
            "data": {
                "slots": [
                    {"slot": 0, "character_name": "A", "character_folder_name": "a", "model_json_path": "a-new.model3.json"},
                    {"slot": 1, "character_name": "B", "character_folder_name": "b", "model_json_path": "b.model3.json"},
                ],
                "changed_slot": 0,
                "preserve_playback": True,
            },
        })
        self.service.run_once()
        self.assertTrue(self.commands.empty())

        self.facts.put({"type": "motion_started", "data": {"renderer_id": "pygame-renderer", "token": active_token}})
        self.service.run_once()
        self.commands.get_nowait()
        self.facts.put({"type": "audio_ended", "data": {"renderer_id": "pygame-renderer", "token": active_token}})
        self.service.run_once()
        switch = self.commands.get_nowait()
        self.assertEqual(switch["type"], "switch_live2d")
        theater_slots = switch["data"]["theater_slots"]
        self.assertEqual(theater_slots[0]["model_json_path"], "a-new.model3.json")
        self.assertEqual(theater_slots[1]["model_json_path"], "b.model3.json")


if __name__ == "__main__":
    unittest.main()
