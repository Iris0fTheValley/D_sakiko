from __future__ import annotations

import asyncio
import os
import sys
import unittest
from queue import Queue

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root not in sys.path:
    sys.path.insert(0, root)

from bridge.saki_bridge import Bridge


class BridgeRuntimeFactTest(unittest.TestCase):
    def test_late_renderer_receives_untargeted_exact_command_snapshot(self):
        bridge = Bridge(Queue())
        bridge._cache_command({
            "v": 2,
            "type": "switch_live2d",
            "data": {"model_url": "model.json", "target_renderer_ids": ["pygame-renderer"]},
        })
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append((writer, message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent[0][0:2], ("electron-ws", "renderer_snapshot"))
        self.assertEqual(sent[0][2]["commands"][0]["data"], {"model_url": "model.json"})

    def test_renderer_fact_is_forwarded_without_controller_specific_filter(self):
        facts = Queue()
        bridge = Bridge(Queue(), renderer_fact_queue=facts)
        asyncio.run(bridge._on_renderer_message({"v": 2, "type": "command_failed", "data": {"token": "cmd", "phase": "motion_start"}}))
        self.assertEqual(facts.get_nowait()["type"], "command_failed")

    def test_renderer_command_queue_is_kept_separate_from_legacy_bridge_events(self):
        commands = Queue()
        bridge = Bridge(Queue(), renderer_command_queue=commands)
        self.assertIs(bridge.renderer_command_queue, commands)

    def test_legacy_pygame_motion_queue_cannot_create_an_electron_executor(self):
        bridge = Bridge(Queue(), motion_queue=Queue())
        self.assertFalse(hasattr(bridge, "_motion_reader_thread"))
        self.assertFalse(hasattr(bridge, "_motion_reader"))


if __name__ == "__main__":
    unittest.main()
