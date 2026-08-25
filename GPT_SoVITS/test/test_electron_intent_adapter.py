from __future__ import annotations
import os, sys, unittest, tempfile, wave
from queue import Queue
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,root) if root not in sys.path else None
from live2d_support.electron_intent_adapter import ElectronIntentAdapter

class ElectronIntentAdapterTest(unittest.TestCase):
 def test_audio_is_paired_only_after_emotion_and_bye_has_no_audio(self):
  emotions,audio,intents=Queue(),Queue(),Queue(); adapter=ElectronIntentAdapter(emotions,audio,intents)
  audio.put("a.wav"); emotions.put("LABEL_0"); self.assertTrue(adapter.run_once()); self.assertEqual(intents.get_nowait()["data"]["audio_path"],"a.wav")
  emotions.put("bye"); self.assertTrue(adapter.run_once()); self.assertEqual(intents.get_nowait()["type"],"bye"); self.assertTrue(audio.empty())
 def test_wav_duration_is_attached_before_shared_scheduler_decides(self):
  file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); file.close()
  try:
   with wave.open(file.name, "wb") as wav: wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\x00\x00" * 48000)
   emotions,audio,intents=Queue(),Queue(),Queue(); adapter=ElectronIntentAdapter(emotions,audio,intents); audio.put(file.name); emotions.put("LABEL_0")
   self.assertTrue(adapter.run_once()); self.assertEqual(intents.get_nowait()["data"]["audio_duration_seconds"], 6.0)
  finally: os.unlink(file.name)
if __name__=='__main__': unittest.main()
