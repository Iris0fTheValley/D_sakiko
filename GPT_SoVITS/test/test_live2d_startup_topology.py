from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Live2DStartupTopologyTest(unittest.TestCase):
    def test_pygame_module_contains_no_business_scheduler_or_random_executor(self):
        source = (ROOT / "live2d_module.py").read_text(encoding="utf-8")
        for forbidden in (
            "SharedBehaviorScheduler(", "SharedLive2DBehavior(",
            "SharedSakikoConversion(", "PygameScheduledMotionExecutor(",
            "PygameSharedSegmentExecutor(", "StartRandomMotion(",
        ):
            self.assertNotIn(forbidden, source)

    def test_main_starts_one_owner_service_and_shared_electron_bridge(self):
        source = (ROOT / "main2.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("authoritative_owner=AuthoritativeLive2DOwner()"), 1)
        self.assertIn("SharedRendererService(", source)
        self.assertIn("FanoutQueue(pygame_renderer_command_queue, electron_renderer_command_queue)", source)
        self.assertIn("electron_bridge.start()", source)

    def test_pygame_filters_renderer_targeted_commands_before_execution(self):
        source = (ROOT / "live2d_module.py").read_text(encoding="utf-8")
        self.assertIn('"pygame-renderer" not in target_ids', source)
        self.assertIn('str(target_id) != "pygame-renderer"', source)

    def test_control_and_thinking_inputs_enter_owner_ingress(self):
        source = (ROOT / "main2.py").read_text(encoding="utf-8")
        self.assertIn("ThinkingStateQueue(multiprocessing.Queue(), owner_intent_queue, thinking_item_count)", source)
        self.assertIn("LegacyControlIntentFanout(", source)
        self.assertIn("control_intent_fanout.run", source)


if __name__ == "__main__":
    unittest.main()
