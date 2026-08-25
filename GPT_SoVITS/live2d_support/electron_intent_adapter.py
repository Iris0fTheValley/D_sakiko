"""Preserve master Pygame emotion/audio FIFO semantics for Electron mode."""
from __future__ import annotations

from queue import Empty


class ElectronIntentAdapter:
    def __init__(self, emotion_queue, audio_queue, intent_queue) -> None:
        self._emotions, self._audio, self._intents = emotion_queue, audio_queue, intent_queue
        self._sequence = 0

    def run_once(self) -> bool:
        try:
            emotion = self._emotions.get_nowait()
        except Empty:
            return False
        if emotion == "bye":
            self._intents.put({"type": "bye", "data": {}})
            return True
        # Deliberately blocks exactly as master Pygame's audio_file_queue.get().
        audio_path = self._audio.get()
        self._sequence += 1
        self._intents.put({
            "type": "emotion_segment",
            "data": {"turn_id": "electron", "segment_id": str(self._sequence), "emotion": str(emotion), "audio_path": str(audio_path)},
        })
        return True
