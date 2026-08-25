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
                    "expression_ids": ["idle", "serious"],
                    "capabilities": {"audio": True},
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
            "transition_groups": ["change_character", "change_character_maskoff"],
            "transition_priority": 2,
        })
        load_commands = [item for item in self.commands if item["type"] == "load_model"]
        self.assertEqual(len(load_commands), 1)
        self.assertEqual(load_commands[0]["data"]["token"], token)
        self.assertEqual(load_commands[0]["data"]["model"]["variant"], "dark")

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
            "transition_groups": ["change_character"],
            "transition_priority": 2,
        })
        load = next(item for item in self.commands if item["type"] == "load_model")
        self._ready_renderer("window-a", "light-ready-a", token)
        self._ready_renderer("window-b", "light-ready-b", token)
        motion = next(item for item in self.commands if item["type"] == "play_motion")
        self.assertEqual(motion["data"]["group"], "change_character")

    def test_new_renderer_receives_confirmed_model_before_commands(self):
        token = self.controller.switch_model({
            "model_url": "http://127.0.0.1:9877/model/sakiko/live2D_model/3.model.json",
            "variant": "light",
            "transition_groups": [],
        })
        self._ready_renderer("window-a", "restore-ready-a", token)
        self._ready_renderer("window-b", "restore-ready-b", token)

        before = len(self.commands)
        self._ready_renderer("window-c", "restore-hello-c")
        restore = [item for item in self.commands[before:] if item["type"] == "load_model"]
        self.assertEqual(len(restore), 1)
        self.assertEqual(restore[0]["data"]["target_renderer_ids"], ["window-c"])
        restore_token = restore[0]["data"]["token"]

        self._ready_renderer("window-c", "restore-ready-c", restore_token)
        self.assertEqual(
            len([item for item in self.commands if item["type"] == "load_model"]),
            2,
        )

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

    def test_audio_has_one_owner_and_mouth_samples_are_fanned_out(self):
        self.controller.start_emotion_segment(
            turn_id="turn-audio",
            segment_id="segment-audio",
            emotion="happiness",
            audio_url="http://127.0.0.1/audio/a.wav",
            audio_duration=1.0,
        )
        audio = next(item for item in self.commands if item["type"] == "play_audio")
        self.assertEqual(audio["data"]["target_renderer_id"], "window-a")
        self.assertTrue(self.controller.handle_renderer_event({
            "type": "mouth_amplitude",
            "event_id": "mouth-a",
            "session_id": "test-session",
            "data": {
                "renderer_id": "window-a",
                "token": audio["data"]["token"],
                "turn_id": "turn-audio",
                "segment_id": "segment-audio",
                "amplitude": 0.42,
            },
        }))
        mouth = [item for item in self.commands if item["type"] == "mouth_amplitude"]
        self.assertEqual(len(mouth), 1)
        self.assertEqual(mouth[0]["data"]["target_renderer_ids"], ["window-a", "window-b"])
        self.assertEqual(mouth[0]["data"]["amplitude"], 0.42)

    def test_renderer_disconnect_finishes_audio_and_special_motion_is_python_selected(self):
        self.controller.start_emotion_segment(
            turn_id="turn-disconnect",
            segment_id="segment-disconnect",
            audio_url="http://127.0.0.1/audio/a.wav",
            audio_duration=1.0,
        )
        self.assertIsNotNone(self.controller.snapshot()["active_audio"])
        self.assertTrue(self.controller.handle_renderer_event({
            "type": "renderer_disconnected",
            "event_id": "disconnect-a",
            "session_id": "test-session",
            "data": {"renderer_id": "window-a"},
        }))
        self.assertIsNone(self.controller.snapshot()["active_audio"])

        self.controller.toggle_sakiko_mask()
        motion = [item for item in self.commands if item["type"] == "play_motion"][-1]["data"]
        self.assertIn(motion["group"], {"change_character_maskoff", "maskon"})
        self.assertNotIn("random", str(motion).lower())

    def test_python_click_gate_is_shared_across_renderers(self):
        first = self.controller.click_motion(event_id="click-one")
        second = self.controller.click_motion(event_id="click-two")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_emotion_expression_is_selected_once_and_restored_to_idle(self):
        self.controller.start_emotion_segment(
            turn_id="turn-expression",
            segment_id="segment-expression",
            emotion="anger",
            motion_group="happiness",
        )
        expression_commands = [item for item in self.commands if item["type"] == "set_expression"]
        self.assertEqual(expression_commands[0]["data"]["expression"], "serious")
        self.assertEqual(
            expression_commands[0]["data"]["target_renderer_ids"],
            ["window-a", "window-b"],
        )

        motion = next(item for item in self.commands if item["type"] == "play_motion")
        for renderer_id in ("window-a", "window-b"):
            self.assertTrue(self.controller.handle_renderer_event({
                "type": "motion_finished",
                "event_id": "expression-finished-" + renderer_id,
                "session_id": "test-session",
                "source": renderer_id,
                "data": {
                    "renderer_id": renderer_id,
                    "token": motion["data"]["token"],
                    "turn_id": "turn-expression",
                    "segment_id": "segment-expression",
                },
            }))

        expression_commands = [item for item in self.commands if item["type"] == "set_expression"]
        self.assertEqual(expression_commands[-1]["data"]["expression"], "idle")

    def test_expression_command_never_uses_unsupported_id(self):
        commands = []
        controller = Live2DBehaviorController(
            commands.append,
            motion_catalog={"happiness": 1},
            session_id="expression-catalog-session",
        )
        controller.handle_renderer_event({
            "type": "renderer_ready",
            "event_id": "expression-catalog-ready",
            "session_id": "expression-catalog-session",
            "data": {
                "renderer_id": "window-a",
                "motion_groups": {"happiness": 1},
                "expression_ids": ["custom_only"],
            },
        })
        controller.start_emotion_segment(
            turn_id="turn-custom-expression",
            segment_id="segment-custom-expression",
            emotion="happiness",
        )
        expression_commands = [item for item in commands if item["type"] == "set_expression"]
        self.assertEqual(expression_commands[0]["data"]["expression"], "")

    def test_stale_disconnect_does_not_remove_reconnected_renderer(self):
        self._ready_renderer_with_connection("window-a", "connection-one", "ready-connection-one")
        self._ready_renderer_with_connection("window-a", "connection-two", "ready-connection-two")
        self.assertFalse(self.controller.handle_renderer_event({
            "type": "renderer_disconnected",
            "event_id": "stale-disconnect",
            "session_id": "test-session",
            "data": {"renderer_id": "window-a", "connection_id": "connection-one"},
        }))
        self.assertIn("window-a", self.controller.snapshot()["renderer_ids"])

    def _ready_renderer_with_connection(self, renderer_id, connection_id, event_id):
        self.assertTrue(self.controller.handle_renderer_event({
            "type": "renderer_ready",
            "event_id": event_id,
            "session_id": "test-session",
            "data": {
                "renderer_id": renderer_id,
                "connection_id": connection_id,
                "motion_groups": self.catalog,
                "expression_ids": ["idle", "serious"],
                "capabilities": {"audio": True},
            },
        }))


if __name__ == "__main__":
    unittest.main()
