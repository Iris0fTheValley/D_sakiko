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
    def test_renderer_fact_is_forwarded_without_controller_specific_filter(self):
        facts = Queue()
        bridge = Bridge(Queue(), renderer_fact_queue=facts)
        asyncio.run(bridge._on_renderer_message({"v": 2, "type": "command_failed", "data": {"token": "cmd", "phase": "motion_start"}}))
        self.assertEqual(facts.get_nowait()["type"], "command_failed")

    def test_renderer_command_queue_is_kept_separate_from_legacy_bridge_events(self):
        commands = Queue()
        bridge = Bridge(Queue(), renderer_command_queue=commands)
        self.assertIs(bridge.renderer_command_queue, commands)


if __name__ == "__main__":
    unittest.main()
