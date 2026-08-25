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

class RendererHostTest(unittest.TestCase):
    def setUp(self):
        self.out = []; self.host = SharedRendererHost(self.out.append, SharedLive2DBehavior(rng=Random(0)))
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
    def test_audio_emits_only_after_matching_motion_start(self):
        self.assertTrue(self.host.start_emotion_segment(turn_id="t",segment_id="s",emotion="LABEL_0",audio_path="a.wav"))
        self.assertEqual(self.out[0]["type"], "play_motion"); token = self.out[0]["data"]["token"]
        self.assertTrue(self.host.handle_renderer_fact({"type":"motion_started","data":{"token":token}}))
        self.assertEqual(self.out[1]["type"], "play_audio"); self.assertEqual(self.out[1]["data"]["path"], "a.wav")
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
        service = SharedRendererService(intents, facts, commands)
        facts.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
        intents.put({"type":"emotion_segment","data":{"turn_id":"t","segment_id":"s","emotion":"LABEL_0","audio_path":"a.wav"}})
        self.assertEqual(service.run_once(), 2)
        motion = commands.get_nowait(); self.assertEqual(motion["type"], "play_motion")
        facts.put({"type":"motion_started","data":{"token":motion["data"]["token"]}})
        self.assertEqual(service.run_once(), 1); self.assertEqual(commands.get_nowait()["type"], "play_audio")
    def test_service_worker_stops_under_caller_lifecycle_control(self):
        service = SharedRendererService(Queue(), Queue(), Queue()); stop = Event()
        worker = Thread(target=service.run, args=(stop,), daemon=True); worker.start()
        time.sleep(0.03); stop.set(); worker.join(0.5)
        self.assertFalse(worker.is_alive())
    def test_bye_closes_only_after_matching_motion_finished(self):
        self.host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"bye":1}}})
        self.assertTrue(self.host.start_bye()); token=self.out[-1]["data"]["token"]
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":"stale"}}); self.assertEqual(self.out[-1]["type"],"play_motion")
        self.host.handle_renderer_fact({"type":"motion_finished","data":{"token":token}}); self.assertEqual(self.out[-1]["type"],"close_renderer")
    def test_click_is_resolved_by_shared_scheduler_not_electron(self):
        host = SharedRendererHost(self.out.append, SharedLive2DBehavior(rng=Random(0)), SharedBehaviorScheduler(clock=lambda: 0.0, rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"renderer_id":"sakiko","motion_groups":{"IDLE":2}}})
        self.assertTrue(host.handle_renderer_fact({"type":"renderer_intent","data":{"intent":"click"}}))
        self.assertEqual((self.out[-1]["type"], self.out[-1]["data"]["group"], self.out[-1]["data"]["index"]), ("play_motion", "IDLE", 1))
    def test_thinking_fact_is_displayed_but_timer_stays_in_shared_scheduler(self):
        clock = type("Clock", (), {"value": 0.0, "__call__": lambda self: self.value})()
        host = SharedRendererHost(self.out.append, SharedLive2DBehavior(rng=Random(0)), SharedBehaviorScheduler(clock=clock, rng=Random(0)))
        host.handle_renderer_fact({"type":"renderer_ready","data":{"motion_groups":{"text_generating":1}}})
        self.assertTrue(host.set_thinking(True)); self.assertEqual(self.out[-1], {"type":"thinking_changed","data":{"active":True}})
        clock.value = 1.0; self.assertTrue(host.tick()); self.assertEqual(self.out[-1]["data"]["group"], "text_generating")

if __name__ == '__main__': unittest.main()
