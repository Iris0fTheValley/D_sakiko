from __future__ import annotations
import os, sys, unittest
from random import Random
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); sys.path.insert(0, script_dir) if script_dir not in sys.path else None
from live2d_support.behavior_scheduler import SharedBehaviorScheduler

class Clock:
    value = 0.0
    def __call__(self): return self.value

class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.s = SharedBehaviorScheduler(clock=self.clock, rng=Random(0))
        self.s.set_catalog({"text_generating": 1, "idle_motion": 1, "IDLE": 2, "happiness": 2})
    def test_thinking_first_then_repeat(self):
        self.s.set_thinking(True); self.clock.value = 0.9; self.assertIsNone(self.s.tick())
        self.clock.value = 1.0; self.assertEqual(self.s.tick().purpose, "thinking")
        self.s.motion_finished("thinking"); self.clock.value = 15.9; self.assertIsNone(self.s.tick())
        self.clock.value = 16.0; self.assertEqual(self.s.tick().purpose, "thinking")
    def test_long_audio_is_fact_gated_and_bounded(self):
        self.s.start_segment("happiness", 6.0); self.s.set_audio_busy(True); self.s.motion_finished("emotion")
        self.clock.value = 2.5; self.assertEqual(self.s.tick().purpose, "long_audio_repeat")
        self.s.motion_finished("long_audio_repeat"); self.clock.value = 5.0; self.assertEqual(self.s.tick().purpose, "long_audio_repeat")
        self.s.motion_finished("long_audio_repeat"); self.clock.value = 7.5; self.assertIsNone(self.s.tick())
    def test_idle_and_click_preserve_master_conditions(self):
        self.clock.value = 2.5; self.assertEqual(self.s.tick().purpose, "idle_recover")
        self.assertIsNone(self.s.click(is_sakiko=False)); self.assertEqual(self.s.click(is_sakiko=True).purpose, "click")
    def test_center_variant_is_resolved_before_executor(self):
        self.s.set_catalog({"IDLE": 1, "IDLE_C": 2})
        self.assertEqual(self.s.click(is_sakiko=True).group, "IDLE_C")

if __name__ == '__main__': unittest.main()
