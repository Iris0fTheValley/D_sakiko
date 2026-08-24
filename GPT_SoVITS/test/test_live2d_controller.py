import random
import unittest

try:
    from live2d_controller import Live2DBehaviorController
except ImportError:
    from GPT_SoVITS.live2d_controller import Live2DBehaviorController


class Live2DControllerTest(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.controller = Live2DBehaviorController(
            self.commands.append,
            rng=random.Random(7),
            motion_catalog={"happiness": 3, "IDLE": 2},
            session_id="test-session",
        )
        for renderer_id in ("window-a", "window-b"):
            self.assertTrue(self.controller.handle_renderer_event({
                "type": "renderer_ready",
                "event_id": "ready-" + renderer_id,
                "session_id": "test-session",
                "source": renderer_id,
                "data": {
                    "renderer_id": renderer_id,
                    "motion_groups": {"happiness": 3, "IDLE": 2},
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


if __name__ == "__main__":
    unittest.main()
