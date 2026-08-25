"""Mirror legacy emotion/audio input into a Pygame baseline and owner shadow."""
from __future__ import annotations

from queue import Empty
import time

from live2d_support.audio_duration import read_audio_duration_seconds


class LegacyEmotionAudioFanout:
    """Consume each legacy pair once, then publish identical ordered inputs.

    During shadow mode Pygame still owns the visible baseline.  The shared
    owner receives an independent copy so it cannot race Pygame for either
    half of the established emotion/audio FIFO contract.
    """

    def __init__(self, emotion_input, audio_input, pygame_emotions, pygame_audio, owner_intents) -> None:
        self._emotion_input = emotion_input
        self._audio_input = audio_input
        self._pygame_emotions = pygame_emotions
        self._pygame_audio = pygame_audio
        self._owner_intents = owner_intents
        self._sequence = 0

    def run_once(self) -> bool:
        try:
            emotion = self._emotion_input.get_nowait()
        except Empty:
            return False
        if emotion == "bye":
            self._pygame_emotions.put(emotion)
            self._owner_intents.put({"type": "bye", "data": {}})
            return True
        # This deliberate wait exactly preserves master Pygame pairing: after
        # consuming an emotion it waits for that segment's audio item.
        audio_path = self._audio_input.get()
        self._sequence += 1
        self._pygame_audio.put(audio_path)
        self._pygame_emotions.put(emotion)
        self._owner_intents.put({
            "type": "emotion_segment",
            "data": {
                "turn_id": "legacy-shadow",
                "segment_id": str(self._sequence),
                "emotion": str(emotion),
                "audio_path": str(audio_path),
                "audio_duration_seconds": read_audio_duration_seconds(str(audio_path)),
            },
        })
        return True

    def run(self, stop_event, poll_interval_seconds: float = 0.02) -> None:
        while not stop_event.is_set():
            if not self.run_once():
                time.sleep(poll_interval_seconds)
