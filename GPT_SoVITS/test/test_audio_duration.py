from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root not in sys.path:
    sys.path.insert(0, root)

from live2d_support.audio_duration import read_audio_duration_seconds


class AudioDurationTest(unittest.TestCase):
    def test_valid_wav_with_zero_rate_does_not_fall_back_to_mixer(self):
        audio = type("Audio", (), {"getframerate": lambda self: 0, "__enter__": lambda self: self, "__exit__": lambda self, *args: None})()
        with patch("live2d_support.audio_duration.os.path.isfile", return_value=True), patch("live2d_support.audio_duration.wave.open", return_value=audio), patch("builtins.__import__") as importer:
            self.assertEqual(read_audio_duration_seconds("zero-rate.wav"), 0.0)
            importer.assert_not_called()

    def test_missing_file_is_zero(self):
        self.assertEqual(read_audio_duration_seconds("missing.wav"), 0.0)


if __name__ == "__main__":
    unittest.main()
