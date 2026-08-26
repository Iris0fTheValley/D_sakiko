from __future__ import annotations
import os, sys, unittest
from random import Random
from queue import Queue
from threading import Event, Thread
import time
root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, root) if root not in sys.path else None
from live2d_support.renderer_host import SharedRendererHost
from live2d_support.renderer_host import SharedRendererService
from live2d_support.shared_behavior import SharedLive2DBehavior
from live2d_support.behavior_scheduler import SharedBehaviorScheduler
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner

class RendererHostTest(unittest.TestCase):
    def setUp(self):
        self.out = []; self.host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
    def test_audio_emits_only_after_matching_motion_start(self):
        self.assertTrue(self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav"))
        self.assertEqual(self.out[0]["type"], "play_motion"); token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"motion_started","data":{"token":token}}))
        self.assertEqual(self.out[1]["type"], "play_audio"); self.assertEqual(self.out[1]["data"]["path"], "a.wav")

    def test_audio_command_exposes_project_path_to_electron_transport(self):
        self.assertTrue(self.host.start_emotion_segment(
            turn_id="t", segment_id="s", emotion="LABEL_0",
            audio_path="../reference_audio/generated_audios_temp/output.wav",
        ))
        token = self.out[0]["data"]["token"]
        self.host.handle_renderer_fact({"type": "motion_started", "data": {"token": token}})
        self.assertEqual(
            self.out[-1]["data"]["electron_audio_url"],
            "http://127.0.0.1:9877/audio/reference_audio/generated_audios_temp/output.wav",
        )

    def test_audio_fanout_selects_one_runtime_owner(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","motion_groups":{"happiness":1}}})
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"random-electron-id","renderer_role":"electron","motion_groups":{"happiness":1}}})
        self.assertTrue(host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav"))
        token = self.out[-1]["data"]["token"]
        host.handle_renderer_fact({"type":"motion_started","data":{"token":token}})
        audio = self.out[-1]
        self.assertEqual(audio["type"], "play_audio")
        self.assertEqual(audio["data"]["target_renderer_id"], "pygame")
        self.assertEqual(audio["data"]["target_renderer_ids"], ["pygame", "random-electron-id"])
    def test_ready_catalog_resolves_expression_outside_renderer(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_files_by_group":{"happiness":["happiness_smile.mtn"]},"expression_ids":["exp_smile01"]}})
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        self.assertEqual(self.out[-1]["data"]["expression_id"], "exp_smile01")
    def test_command_failure_is_consumed(self):
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"command_failed","data":{"token":token,"phase":"audio_start"}}))

    def test_raw_motion_start_failure_still_runs_master_audio_fallback(self):
        self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav")
        token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"command_failed","data":{"token":token,"phase":"motion_start"}}))
        self.assertEqual(self.out[-1]["type"], "play_audio")
        self.assertEqual(self.out[-1]["data"]["path"], "a.wav")

    def test_service_turns_queue_intent_and_fact_into_bridge_commands(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        self.assertEqual(service.run_once(), 2)
        motion = commands.get_nowait(); self.assertEqual(motion["type"], "play_motion")
        facts.put({"type":"motion_started","data":{"token":motion["data"]["token"]}})
        self.assertEqual(service.run_once(), 1); self.assertEqual(commands.get_nowait()["type"], "play_audio")

    def test_service_defers_legacy_intent_until_renderer_capabilities_arrive(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        self.assertEqual(service.run_once(), 0)
        self.assertTrue(commands.empty())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
        self.assertEqual(service.run_once(), 2)
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_worker_stops_under_caller_lifecycle_control(self):
        service = SharedRendererService(Queue(), Queue(), Queue(), AuthoritativeLive2DOwner()); stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True); worker.start()
        time.sleep(0.03); stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())

    def test_service_drains_shutdown_bye_before_stopping(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        intents.put({"type":"bye","data":{}})
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start(); time.sleep(0.03); stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_acknowledges_bye_before_stop_without_sleep(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "play_motion")

    def test_service_consumes_bye_when_renderer_never_becomes_ready(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait(), {"type": "close_renderer", "data": {"reason": "bye_motion_unavailable"}})

    def test_shutdown_control_overtakes_deferred_emotion_when_not_ready(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True)
        worker.start()
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        intents.put({"type":"bye","data":{}})
        self.assertTrue(service.wait_for_bye(0.5))
        stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(commands.get_nowait()["type"], "close_renderer")
    def test_bye_closes_only_after_matching_motion_finished(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        self.assertTrue(self.host.start_bye()); token=self.out[-1]["data"]["token"]
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":"stale"}}); self.assertEqual(self.out[-1]["type"],"play_motion")
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":token}}); self.assertEqual(self.out[-1]["type"],"close_renderer")
    def test_click_is_resolved_by_shared_scheduler_not_electron(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(clock=lambda: 0.0, rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"model_key":"sakiko","motion_groups":{"IDLE":2}}})
        self.assertTrue(host.handle_renderer_fact({"type":"renderer_intent","data":{"intent":"click"}}))
        self.assertEqual((self.out[-1]["type"], self.out[-1]["data"]["group"], self.out[-1]["data"]["index"]), ("play_motion", "IDLE", 1))

    def test_click_uses_canonical_pygame_model_when_renderer_facts_disagree(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_key":"normal","motion_groups":{"IDLE":2}}})
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","renderer_role":"electron","model_key":"sakiko","motion_groups":{"IDLE":2}}})
        self.assertFalse(host.handle_renderer_fact({"type":"renderer_intent","data":{"intent":"click"}}))

    def test_pygame_catalog_remains_canonical_when_electron_ready_arrives_last(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","motion_files_by_group":{"happiness":["pygame.mtn"]}}})
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","renderer_role":"electron","motion_files_by_group":{"happiness":["electron-a.mtn","electron-b.mtn"]}}})
        self.assertTrue(host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav"))
        self.assertEqual(self.out[-1]["data"]["index"], 0)
    def test_thinking_fact_is_displayed_but_timer_stays_in_shared_scheduler(self):
        clock = type("Clock", (), {"value": 0.0, "__call__": lambda self: self.value})()
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(clock=clock, rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"text_generating":1}}})
        self.assertTrue(host.set_thinking(True)); self.assertEqual(self.out[-1], {"type":"thinking_changed","data":{"active":True}})
        clock.value = 1.0; self.assertTrue(host.tick()); self.assertEqual(self.out[-1]["data"]["group"], "text_generating")
    def test_conversion_waits_for_reloaded_renderer_catalog_before_exact_motion(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(clock=lambda: 0.0, rng=Random(1)))
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"white.model.json"})); self.assertEqual(self.out[-1]["type"], "switch_live2d")
        self.assertEqual(self.out[-1]["data"]["character_folder_name"], "sakiko")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"sakiko","motion_groups":{"change_character":1}}})
        self.assertEqual((self.out[-1]["type"],self.out[-1]["data"]["group"],self.out[-1]["data"]["priority"]), ("play_motion","change_character",2))

    def test_late_renderer_is_replayed_into_pending_conversion(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":"old","model_urls":{"white":"white.model.json"},"motion_groups":{"change_character":1}}})
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"white.model.json"}))
        switch = self.out[-1]
        token = switch["data"]["model_token"]
        self.assertEqual(switch["type"], "switch_live2d")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"late-electron","renderer_role":"electron","model_token":"old","motion_groups":{"change_character":1}}})
        replay = self.out[-1]
        self.assertEqual(replay["type"], "switch_live2d")
        self.assertEqual(replay["data"]["target_renderer_ids"], ["late-electron"])
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":token,"motion_groups":{"change_character":1}}})
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"late-electron","renderer_role":"electron","model_token":token,"motion_groups":{"change_character":1}}})
        self.assertEqual(self.out[-1]["type"], "play_motion")

    def test_renderer_joining_after_conversion_completion_replays_exact_commands(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":"old","model_urls":{"white":"white.model.json"},"motion_groups":{"change_character":1}}})
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"white.model.json"}))
        token = self.out[-1]["data"]["model_token"]
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":token,"motion_groups":{"change_character":1}}})
        first_motion = self.out[-1]
        self.assertEqual(first_motion["type"], "play_motion")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"late-electron","renderer_role":"electron","model_token":"old","motion_groups":{"change_character":1}}})
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        self.assertEqual(self.out[-1]["data"]["target_renderer_ids"], ["late-electron"])
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"late-electron","renderer_role":"electron","model_token":token,"motion_groups":{"change_character":1}}})
        replay_motion = self.out[-1]
        self.assertEqual(replay_motion["type"], "play_motion")
        self.assertEqual(replay_motion["data"]["target_renderer_ids"], ["late-electron"])
        self.assertEqual(replay_motion["data"]["token"], first_motion["data"]["token"])

    def test_restarted_renderer_instance_replays_conversion_motion_with_same_renderer_id(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","renderer_role":"electron","renderer_instance_id":"one","model_token":"old","model_urls":{"white":"white.model.json"},"motion_groups":{"change_character":1}}})
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"white.model.json"}))
        token = self.out[-1]["data"]["model_token"]
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","renderer_role":"electron","renderer_instance_id":"one","model_token":token,"motion_groups":{"change_character":1}}})
        first_motion = self.out[-1]
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","renderer_role":"electron","renderer_instance_id":"two","model_token":token,"motion_groups":{"change_character":1}}})
        replay_motion = self.out[-1]
        self.assertEqual(replay_motion["type"], "play_motion")
        self.assertEqual(replay_motion["data"]["target_renderer_ids"], ["electron"])
        self.assertEqual(replay_motion["data"]["token"], first_motion["data"]["token"])

    def test_reconnect_ready_does_not_reset_an_active_segment(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","motion_groups":{"happiness":1}}})
        self.assertTrue(self.host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav"))
        token = self.out[-1]["data"]["token"]
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","motion_groups":{"happiness":1}}})
        self.host.handle_renderer_fact({"type":"motion_started","data":{"renderer_id":"pygame","token":token}})
        self.assertEqual(self.out[-1]["type"], "play_audio")

    def test_multiple_renderers_share_one_owner_and_receive_fanout_commands(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","motion_groups":{"happiness":1}}})
        self.assertTrue(self.host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"electron","motion_groups":{"happiness":1}}}))
        self.assertTrue(self.host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav"))
        self.assertEqual(self.out[-1]["data"]["target_renderer_ids"], ["electron", "pygame"])

    def test_conversion_waits_for_every_renderer_with_matching_model_token(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":"old","model_urls":{"white":"pygame-white.model.json"},"motion_groups":{"change_character":1}}})
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"random-electron-id","renderer_role":"electron","model_token":"old","model_urls":{"white":"http://127.0.0.1:9877/model/sakiko/live2D_model/3.model.json"},"motion_groups":{"change_character":1}}})
        self.assertTrue(host.start_sakiko_conversion(False, {"white":"pygame-white.model.json"}))
        switch = self.out[-1]
        self.assertEqual(switch["data"]["electron_model_url"], "http://127.0.0.1:9877/model/sakiko/live2D_model/3.model.json")
        token = switch["data"]["model_token"]
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"pygame","renderer_role":"pygame","model_token":token,"motion_groups":{"change_character":1}}})
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"random-electron-id","renderer_role":"electron","model_token":token,"motion_groups":{"change_character":1}}})
        self.assertEqual(self.out[-1]["type"], "play_motion")

    def test_cancel_and_stop_talking_release_scheduler_reservations(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_role": "electron",
            "motion_groups": {"talking_motion": 1, "idle_motion": 1},
        }})
        self.assertTrue(host.handle_runtime_control({"type": "start_talking"}))
        talking_token = self.out[-1]["data"]["token"]
        self.assertTrue(host.handle_runtime_control({"type": "stop_talking"}))
        self.assertFalse(host.handle_renderer_fact({"type": "motion_finished", "data": {"token": talking_token}}))
        self.assertTrue(host.handle_runtime_control({"type": "cancel_turn"}))
        self.assertFalse(host.tick())

    def test_electron_is_the_single_audio_owner_when_pygame_is_absent(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_role": "electron",
            "motion_groups": {"happiness": 1},
        }})
        self.assertTrue(host.start_emotion_segment(
            turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav",
        ))
        token = self.out[-1]["data"]["token"]
        host.handle_renderer_fact({"type": "motion_started", "data": {"renderer_id": "electron", "token": token}})
        self.assertEqual(self.out[-1]["type"], "play_audio")
        self.assertEqual(self.out[-1]["data"]["target_renderer_id"], "electron")

    def test_first_motion_fact_selects_audio_owner_without_role_precedence(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "motion_groups": {"happiness": 1},
        }})
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_role": "electron", "motion_groups": {"happiness": 1},
        }})
        host.start_emotion_segment(turn_id="t", segment_id="s", emotion="LABEL_0", audio_path="a.wav")
        token = self.out[-1]["data"]["token"]
        host.handle_renderer_fact({"type": "motion_started", "data": {"renderer_id": "electron", "token": token}})
        self.assertEqual(self.out[-1]["data"]["target_renderer_id"], "electron")

    def test_renderer_hello_receives_canonical_model_switch(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "model_json": "C:/app/live2d_related/sakiko/live2D_model/3.model.json",
            "model_urls": {"model_json": "C:/app/live2d_related/sakiko/live2D_model/3.model.json"},
            "motion_groups": {"happiness": 1},
        }})
        host.handle_renderer_fact({"type": "renderer_hello", "data": {
            "renderer_id": "electron", "renderer_role": "electron", "renderer_instance_id": "new",
        }})
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        self.assertEqual(self.out[-1]["data"]["target_renderer_ids"], ["electron"])
        self.assertTrue(self.out[-1]["data"]["electron_model_url"].startswith("http://127.0.0.1:9877/model/"))

    def test_stale_renderer_instance_facts_are_rejected_after_reconnect(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_hello", "data": {
            "renderer_id": "electron", "renderer_instance_id": "new", "renderer_role": "electron",
        }})
        self.assertTrue(host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_instance_id": "new", "motion_groups": {"happiness": 1},
        }}))
        self.assertFalse(host.handle_renderer_fact({"type": "motion_started", "data": {
            "renderer_id": "electron", "renderer_instance_id": "old", "token": "stale",
        }}))

    def test_disconnected_renderer_does_not_block_conversion_barrier(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(1)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_token": "old",
            "model_urls": {"white": "white.model.json"},
            "motion_groups": {"change_character": 1},
        }})
        self.assertTrue(host.start_sakiko_conversion(False, {"white": "white.model.json"}))
        host.handle_renderer_fact({"type": "renderer_disconnected", "data": {"renderer_id": "pygame"}})
        self.assertEqual(self.out[-1]["type"], "play_motion")
        self.assertFalse(host.handle_renderer_fact({"type": "motion_finished", "data": {
            "renderer_id": "pygame", "token": self.out[-1]["data"]["token"],
        }}))

    def test_unavailable_renderer_is_removed_from_execution_targets(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "renderer_role": "pygame", "motion_groups": {"happiness": 1},
        }})
        self.assertTrue(host.handle_renderer_fact({"type": "renderer_unavailable", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one",
            "reason": "live2d_model_unavailable",
        }}))
        self.assertFalse(host.handle_renderer_fact({"type": "motion_started", "data": {
            "renderer_id": "pygame", "renderer_instance_id": "one", "token": "stale",
        }}))

    def test_secondary_electron_receives_canonical_model_handshake(self):
        host = SharedRendererHost(self.out.append, AuthoritativeLive2DOwner(rng=Random(0)))
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "pygame", "renderer_role": "pygame", "model_key": "sakiko",
            "model_token": "canonical", "model_json": "white.model.json",
            "model_urls": {"white": "white.model.json", "black": "black.model.json"},
            "motion_groups": {"happiness": 1},
        }})
        host.handle_renderer_fact({"type": "renderer_ready", "data": {
            "renderer_id": "electron", "renderer_role": "electron", "model_key": "other",
            "model_token": "stale", "motion_groups": {"happiness": 1},
        }})
        self.assertEqual(self.out[-1]["type"], "switch_live2d")
        self.assertEqual(self.out[-1]["data"]["target_renderer_ids"], ["electron"])
        self.assertEqual(self.out[-1]["data"]["model_token"], "canonical")

if __name__ == '__main__': unittest.main()
