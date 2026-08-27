from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Live2DStartupTopologyTest(unittest.TestCase):
    def test_legacy_electron_business_wrappers_are_removed(self):
        support = ROOT / "live2d_support"
        self.assertFalse((support / "electron_renderer_runtime.py").exists())
        self.assertFalse((support / "electron_intent_adapter.py").exists())

    def test_runtime_launcher_keeps_renderer_selection_outside_business_owner(self):
        source = (ROOT.parent / "tools" / "launch_runtime.py").read_text(encoding="utf-8")
        self.assertIn("DSAKIKO_RENDERER", source)
        self.assertIn("DSAKIKO_DUAL_RENDERER", source)
        self.assertIn('if mode == "electron" or dual_renderer_enabled():', source)
        self.assertIn("electron_process = subprocess.Popen", source)
        self.assertIn("python_process = subprocess.Popen", source)

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
        self.assertIn("project_root not in sys.path", source)
        self.assertEqual(source.count("authoritative_owner=AuthoritativeLive2DOwner()"), 1)
        self.assertIn("SharedRendererService(", source)
        self.assertIn("renderer_command_fanout", source)
        self.assertIn("FanoutQueue(electron_renderer_command_queue)", source)
        self.assertIn("electron_bridge.start()", source)
        self.assertEqual(source.count("build_initial_live2d_intent(characters)"), 1)
        self.assertIn("owner_intent_queue.put(initial_live2d_intent)", source)
        self.assertIn("authoritative_owner, motion_complete_value", source)
        self.assertNotIn("change_char_queue.put('exit')", source)

    def test_pygame_bootstrap_waits_for_authoritative_model_command(self):
        source = (ROOT / "live2d_module.py").read_text(encoding="utf-8")
        initialize = source.split("def live2D_initialize", 1)[1].split("def _reset_eye_open_transition", 1)[0]
        self.assertNotIn("switch_live2d_target(", initialize)
        self.assertIn("loaded only after the authoritative owner sends switch_live2d", initialize)

    def test_pygame_filters_renderer_targeted_commands_before_execution(self):
        source = (ROOT / "live2d_module.py").read_text(encoding="utf-8")
        self.assertIn('"pygame-renderer" not in target_ids', source)
        self.assertIn('str(target_id) != "pygame-renderer"', source)
        self.assertIn('{"type": "switch_live2d", **switch_data}', source)
        self.assertIn("adapter = PygameRendererCommandAdapter(model", source)
        self.assertNotIn("if isinstance(model, Live2DModelAdapter) else None", source)
        self.assertNotIn("self.switch_live2d_target(self.character_list[0].character_name)", source)
        self.assertNotIn("motion_complete_value.value =", source)
        self.assertNotIn("live2d_this_turn_motion_complete", source)
        self.assertIn('"type": "renderer_hello"', source)

    def test_electron_uses_authoritative_character_folder_name_for_switches(self):
        source = (ROOT.parent / "electron_frontend" / "src" / "renderer" / "App.vue").read_text(encoding="utf-8")
        self.assertIn("commandData.character_folder ?? commandData.character_folder_name ?? currentCharKey.value", source)

    def test_control_and_thinking_inputs_enter_owner_ingress(self):
        source = (ROOT / "main2.py").read_text(encoding="utf-8")
        self.assertIn("ThinkingStateQueue(multiprocessing.Queue(), owner_intent_queue, thinking_item_count)", source)
        self.assertIn("LegacyControlIntentFanout(", source)
        self.assertIn("control_intent_fanout.run", source)


if __name__ == "__main__":
    unittest.main()
