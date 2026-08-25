from __future__ import annotations
import os, sys, unittest
from queue import Queue
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,root) if root not in sys.path else None
from live2d_support.electron_intent_adapter import ElectronIntentAdapter

class ElectronIntentAdapterTest(unittest.TestCase):
 def test_audio_is_paired_only_after_emotion_and_bye_has_no_audio(self):
  emotions,audio,intents=Queue(),Queue(),Queue(); adapter=ElectronIntentAdapter(emotions,audio,intents)
  audio.put("a.wav"); emotions.put("LABEL_0"); self.assertTrue(adapter.run_once()); self.assertEqual(intents.get_nowait()["data"]["audio_path"],"a.wav")
  emotions.put("bye"); self.assertTrue(adapter.run_once()); self.assertEqual(intents.get_nowait()["type"],"bye"); self.assertTrue(audio.empty())
if __name__=='__main__': unittest.main()
