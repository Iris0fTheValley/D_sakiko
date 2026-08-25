from __future__ import annotations

import os
import sys
import unittest
from random import Random

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.segment_lifecycle import SharedSegmentLifecycle
from live2d_support.shared_behavior import SharedLive2DBehavior


class SharedSegmentLifecycleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.behavior = SharedLive2DBehavior(rng=Random(0))
        self.behavior.set_capabilities({"happiness": 1})

    def command(self):
        command = self.behavior.start_emotion_segment(
            turn_id="turn", segment_id="segment", emotion="LABEL_0", audio_path="answer.wav",
        )
        assert command is not None
        return command

    def test_motion_start_starts_audio_and_real_busy_edge_finishes_segment(self) -> None:
        started_paths = []
        lifecycle = SharedSegmentLifecycle(self.behavior, lambda audio: started_paths.append(audio.audio_path) or True)
        command = self.command()
        self.assertTrue(lifecycle.consume_motion_fact("motion_started", command.command_id))
        self.assertEqual(started_paths, ["answer.wav"])
        self.assertFalse(self.behavior.legacy_motion_complete)
        self.assertFalse(lifecycle.observe_audio_busy(True))
        self.assertTrue(lifecycle.observe_audio_busy(False))
        self.assertTrue(self.behavior.legacy_motion_complete)
        self.assertIsNone(self.behavior.active_command)

    def test_audio_failure_clears_pending_segment_and_stale_fact_is_ignored(self) -> None:
        lifecycle = SharedSegmentLifecycle(self.behavior, lambda audio: False)
        command = self.command()
        self.assertFalse(lifecycle.consume_motion_fact("motion_rejected", command.command_id))
        self.assertIsNone(self.behavior.active_command)
        self.assertFalse(lifecycle.consume_motion_fact("motion_finished", command.command_id))


if __name__ == "__main__":
    unittest.main()
