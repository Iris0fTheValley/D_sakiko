from __future__ import annotations

import asyncio
import os
import sys
import unittest
from queue import Queue

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root not in sys.path:
    sys.path.insert(0, root)
gpt_root = os.path.join(root, "GPT_SoVITS")
if gpt_root not in sys.path:
    sys.path.insert(0, gpt_root)

from bridge.saki_bridge import Bridge
from live2d_support.authoritative_owner import AuthoritativeLive2DOwner
from live2d_support.renderer_host import SharedRendererHost


class BridgeRuntimeFactTest(unittest.TestCase):
    def test_model_switch_is_never_replayed_by_bridge_snapshot(self):
        bridge = Bridge(Queue())
        for targets in (["pygame-renderer"], ["electron-one"], None):
            data = {"model_url": "model.json"}
            if targets is not None:
                data["target_renderer_ids"] = targets
            bridge._cache_command({"v": 2, "type": "switch_live2d", "data": data})
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append((writer, message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent, [])

    def test_late_renderer_receives_untargeted_thinking_snapshot(self):
        bridge = Bridge(Queue())
        bridge._cache_command({
            "v": 2,
            "type": "thinking_changed",
            "data": {"active": True, "target_renderer_ids": ["electron-one"]},
        })
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append((writer, message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent[0][0:2], ("electron-ws", "renderer_snapshot"))
        self.assertEqual(sent[0][2]["commands"][0]["data"], {"active": True})

    def test_pygame_first_dual_connect_executes_one_electron_model_switch(self):
        emitted = []
        host = SharedRendererHost(emitted.append, AuthoritativeLive2DOwner())
        host.handle_runtime_control({
            "type": "switch_live2d",
            "initial_model": True,
            "model_json": "C:/app/live2d_related/anon/live2D_model/3.model.json",
            "model_token": "initial-token",
        })
        host.handle_renderer_fact({"type": "renderer_hello", "data": {
            "renderer_id": "pygame-renderer", "renderer_role": "pygame",
            "renderer_instance_id": "pygame-one",
        }})
        pygame_switch = emitted[-1]
        self.assertEqual(pygame_switch["data"]["target_renderer_ids"], ["pygame-renderer"])

        bridge = Bridge(Queue())
        bridge._cache_command(pygame_switch)
        snapshots = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                snapshots.append((message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(snapshots, [])

        host.handle_renderer_fact({"type": "renderer_hello", "data": {
            "renderer_id": "electron", "renderer_role": "electron",
            "renderer_instance_id": "electron-one", "capabilities": ["snapshot"],
        }})
        electron_switches = [
            command for command in emitted
            if command.get("type") == "switch_live2d"
            and command.get("data", {}).get("target_renderer_ids") == ["electron"]
        ]
        self.assertEqual(len(electron_switches), 1)
        self.assertEqual(electron_switches[0]["data"]["model_token"], "initial-token")

    def test_snapshot_does_not_cache_motion_without_owner_lifecycle_fact(self):
        bridge = Bridge(Queue())
        bridge._cache_command({"type": "play_motion", "data": {"token": "stale"}})
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append(data)

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent, [])

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

    def test_renderer_disconnect_fact_is_emitted_for_hello_writer(self):
        facts = Queue()
        bridge = Bridge(Queue(), renderer_fact_queue=facts)
        writer = object()
        asyncio.run(bridge._on_renderer_message({
            "type": "renderer_hello",
            "data": {"renderer_id": "electron", "renderer_instance_id": "instance-1"},
        }, writer))
        self.assertEqual(facts.get_nowait()["type"], "renderer_hello")
        asyncio.run(bridge._on_renderer_disconnect(writer))
        disconnected = facts.get_nowait()
        self.assertEqual(disconnected["type"], "renderer_disconnected")
        self.assertEqual(disconnected["data"], {"renderer_id": "electron", "renderer_instance_id": "instance-1"})


if __name__ == "__main__":
    unittest.main()
