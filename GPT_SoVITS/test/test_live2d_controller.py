import random
import unittest

try:
    from live2d_controller import BehaviorConfig, Live2DBehaviorController
except ImportError:
    from GPT_SoVITS.live2d_controller import BehaviorConfig, Live2DBehaviorController


class Live2DControllerTest(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.catalog = {
            "happiness": 3,
            "IDLE": 2,
            "change_character": 3,
            "change_character_maskoff": 2,
        }
        self.controller = Live2DBehaviorController(
            self.commands.append,
            rng=random.Random(7),
            motion_catalog=self.catalog,
            session_id="test-session",
        )
        for renderer_id in ("window-a", "window-b"):
            self._ready_renderer(renderer_id, "ready-" + renderer_id)

    def _ready_renderer(self, renderer_id, event_id, model_token=""):
        self.assertTrue(self.controller.handle_renderer_event({
                "type": "renderer_ready",
                "event_id": event_id,
                "session_id": "test-session",
                "source": renderer_id,
                "data": {
                    "renderer_id": renderer_id,
                    "model_token": model_token,
                    "motion_groups": self.catalog,
                    "capabilities": {"audio": False},
                },
            }))

    def test_random_index_is_selected_once_and_both_windows_finish(self):
        self.controller.start_emotion_segment(
            turn_id="turn-1", segment_id="segment-1", emotion="happiness", text="hello"
        )
        motions = [item for item in self.commands if item["type"] == "play_motion"]
        self.assertEqual(len(motions), 1)
        motion = motions[0]
        self.assertEqual(motion["data"]["target_renderer_ids"], ["window-a", "window-b"])

        for renderer_id in ("window-a", "window-b"):
            self.assertTrue(self.controller.handle_renderer_event({
                "type": "motion_finished",
                "event_id": "finished-" + renderer_id,
                "session_id": "test-session",
                "source": renderer_id,
                "data": {
                    "renderer_id": renderer_id,
                    "token": motion["data"]["token"],
                    "turn_id": "turn-1",
                    "segment_id": "segment-1",
                },
            }))

        self.assertEqual(sum(item["type"] == "segment_completed" for item in self.commands), 1)

    def test_stale_motion_fact_is_rejected(self):
        self.controller.start_emotion_segment(
            turn_id="turn-1", segment_id="segment-1", emotion="happiness"
        )
        first = next(item for item in self.commands if item["type"] == "play_motion")
        self.controller.start_emotion_segment(
            turn_id="turn-1", segment_id="segment-2", emotion="happiness"
        )
        self.assertFalse(self.controller.handle_renderer_event({
            "type": "motion_finished",
            "event_id": "stale-finish",
            "session_id": "test-session",
            "source": "window-a",
            "data": {"token": first["data"]["token"], "renderer_id": "window-a"},
        }))

    def test_dark_sakiko_switch_waits_for_both_renderers_and_selects_once(self):
        token = self.controller.switch_model({
            "model_url": "http://127.0.0.1:9877/model/sakiko/live2D_model_costume/3.model.json",
            "variant": "dark",
            "initial_expression": "serious",
            "transition_groups": ["change_character", "change_character_maskoff"],
            "transition_priority": 2,
        })
        load_commands = [item for item in self.commands if item["type"] == "load_model"]
        self.assertEqual(len(load_commands), 1)
        self.assertEqual(load_commands[0]["data"]["token"], token)
        self.assertEqual(load_commands[0]["data"]["model"]["variant"], "dark")
        self.assertEqual(load_commands[0]["data"]["model"]["initial_expression"], "serious")

        self._ready_renderer("window-a", "dark-ready-a", token)
        self.assertFalse(any(item["type"] == "play_motion" for item in self.commands))
        self._ready_renderer("window-b", "dark-ready-b", token)

        motions = [item for item in self.commands if item["type"] == "play_motion"]
        self.assertEqual(len(motions), 1)
        motion = motions[0]["data"]
        self.assertIn(motion["group"], {"change_character", "change_character_maskoff"})
        self.assertEqual(motion["priority"], 2)
        self.assertEqual(motion["target_renderer_ids"], ["window-a", "window-b"])
        self.assertLess(motion["index"], self.catalog[motion["group"]])

    def test_light_sakiko_switch_uses_idle_and_change_character(self):
        token = self.controller.switch_model({
            "model_url": "http://127.0.0.1:9877/model/sakiko/live2D_model/3.model.json",
            "variant": "light",
            "initial_expression": "idle",
            "transition_groups": ["change_character"],
            "transition_priority": 2,
        })
        load = next(item for item in self.commands if item["type"] == "load_model")
        self.assertEqual(load["data"]["model"]["initial_expression"], "idle")
        self._ready_renderer("window-a", "light-ready-a", token)
        self._ready_renderer("window-b", "light-ready-b", token)
        motion = next(item for item in self.commands if item["type"] == "play_motion")
        self.assertEqual(motion["data"]["group"], "change_character")

    def test_model_switch_timeout_does_not_leave_controller_switching(self):
        now = [10.0]
        commands = []
        controller = Live2DBehaviorController(
            commands.append,
            clock=lambda: now[0],
            session_id="timeout-session",
            motion_catalog={"change_character": 1},
            config=BehaviorConfig(model_switch_timeout=1.0),
        )
        controller.switch_model({"model_url": "missing", "transition_groups": ["change_character"]})
        self.assertEqual(controller.snapshot()["state"], "switching")
        now[0] = 11.1
        controller.tick()
        self.assertEqual(controller.snapshot()["state"], "idle")
        self.assertEqual(sum(item["type"] == "model_switch_failed" for item in commands), 1)

    def test_theme_color_is_broadcast_without_changing_behavior_state(self):
        before = self.controller.snapshot()["state"]
        event_id = self.controller.set_theme_color("#12abEF")
        theme_commands = [item for item in self.commands if item["type"] == "set_theme_color"]
        self.assertEqual(len(theme_commands), 1)
        self.assertEqual(theme_commands[0]["event_id"], event_id)
        self.assertEqual(theme_commands[0]["data"]["theme_color"], "#12ABEF")
        self.assertEqual(theme_commands[0]["data"]["target_renderer_ids"], ["window-a", "window-b"])
        self.assertEqual(self.controller.snapshot()["state"], before)
        with self.assertRaises(ValueError):
            self.controller.set_theme_color("blue")


if __name__ == "__main__":
    unittest.main()
