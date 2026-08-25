from __future__ import annotations
import os, sys, unittest
from queue import Queue
root=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); sys.path.insert(0,root) if root not in sys.path else None
from live2d_support.electron_renderer_runtime import ElectronRendererRuntime

class ElectronRendererRuntimeTest(unittest.TestCase):
 def test_wires_master_queues_to_shared_command_queue(self):
  emotions,audio=Queue(),Queue(); runtime=ElectronRendererRuntime(emotions,audio)
  runtime.renderer_fact_queue.put({"type":"renderer_ready","data":{"motion_groups":{"happiness":1}}})
  runtime.pump_once(); audio.put("a.wav"); emotions.put("LABEL_0"); self.assertEqual(runtime.pump_once(),2)
  self.assertEqual(runtime.command_queue.get_nowait()["type"],"play_motion")
 def test_projects_existing_thinking_queue_only_on_state_edges(self):
  emotions,audio,thinking=Queue(),Queue(),Queue(); runtime=ElectronRendererRuntime(emotions,audio,thinking)
  runtime.renderer_fact_queue.put({"type":"renderer_ready","data":{"motion_groups":{"text_generating":1}}})
  self.assertEqual(runtime.pump_once(), 3); self.assertEqual(runtime.command_queue.get_nowait(), {"type":"thinking_changed","data":{"active":False}})
  self.assertEqual(runtime.pump_once(), 0)
  thinking.put("no_complete"); self.assertEqual(runtime.pump_once(), 2); self.assertEqual(runtime.command_queue.get_nowait(), {"type":"thinking_changed","data":{"active":True}})
if __name__=='__main__': unittest.main()
