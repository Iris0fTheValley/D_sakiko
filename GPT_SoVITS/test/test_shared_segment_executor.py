from __future__ import annotations

import os
import sys
import unittest

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.shared_segment_executor import PygameRendererCommandAdapter


class FakeRuntime:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.calls = []

    def set_expression_if_supported(self, expression_id: str) -> bool:
        self.calls.append(("expression", expression_id))
        return True

    def StartMotion(self, group_name, motion_index, priority, on_start, on_finish, position, auto_expression):
        self.calls.append(("motion", group_name, motion_index, priority, position, auto_expression))
        if self.accepted:
            on_start()
            on_finish()
        return self.accepted


class PygameRendererCommandAdapterTestCase(unittest.TestCase):
    def test_audio_only_fallback_does_not_call_the_motion_runtime(self) -> None:
        runtime = FakeRuntime(True)
        facts = []
        adapter = PygameRendererCommandAdapter(runtime, facts.append)
        self.assertFalse(adapter.execute({"type":"play_motion","data":{"token":"cmd","group":"","index":0}}))
        self.assertEqual(runtime.calls, [])
        self.assertEqual(facts[-1]["type"], "command_failed")

    def test_contract_adapter_executes_no_local_choice(self) -> None:
        runtime, facts = FakeRuntime(True), []
        self.assertTrue(PygameRendererCommandAdapter(runtime, facts.append).execute({"type":"play_motion","data":{"token":"t","group":"IDLE_C","index":1,"priority":1,"expression_id":"exp_smile01"}}))
        self.assertEqual(runtime.calls, [("expression", "exp_smile01"), ("motion", "IDLE_C", 1, 1, None, False)])
        self.assertEqual([fact["type"] for fact in facts], ["motion_started", "motion_finished"])

    def test_contract_adapter_starts_exact_audio_and_reports_failure(self) -> None:
        runtime, facts, started = FakeRuntime(True), [], []
        adapter = PygameRendererCommandAdapter(runtime, facts.append, lambda path: started.append(path) is None)
        self.assertTrue(adapter.execute({"type":"play_audio","data":{"token":"t","path":"a.wav"}}))
        self.assertEqual(started, ["a.wav"])
        self.assertEqual(facts, [{"type":"audio_started","data":{"token":"t"}}])
        self.assertFalse(PygameRendererCommandAdapter(runtime, facts.append).execute({"type":"play_audio","data":{"token":"t","path":"a.wav"}}))
        self.assertEqual(facts[-1]["data"]["phase"], "audio_start")


if __name__ == "__main__":
    unittest.main()
