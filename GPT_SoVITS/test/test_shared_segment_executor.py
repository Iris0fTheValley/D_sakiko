from __future__ import annotations

import os
import sys
import unittest

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.shared_behavior import ExactMotion, PlaySegment
from live2d_support.shared_segment_executor import PygameSharedSegmentExecutor
from live2d_support.shared_segment_executor import PygameScheduledMotionExecutor
from live2d_support.shared_segment_executor import PygameRendererCommandAdapter
from live2d_support.behavior_scheduler import ScheduledMotion


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


def command() -> PlaySegment:
    return PlaySegment("cmd", "turn", "segment", ExactMotion("happiness_C", 1, expression_id="exp_smile01"), "a.wav", 1.0)


class PygameSharedSegmentExecutorTestCase(unittest.TestCase):
    def test_executes_exact_motion_without_random_or_auto_expression(self) -> None:
        runtime = FakeRuntime(True)
        facts = []
        self.assertTrue(PygameSharedSegmentExecutor(runtime, lambda kind, token: facts.append((kind, token))).execute(command()))
        self.assertEqual(runtime.calls, [
            ("expression", "exp_smile01"),
            ("motion", "happiness_C", 1, 3, None, False),
        ])
        self.assertEqual(facts, [("motion_started", "cmd"), ("motion_finished", "cmd")])

    def test_rejection_is_reported_as_a_fact(self) -> None:
        runtime = FakeRuntime(False)
        facts = []
        self.assertFalse(PygameSharedSegmentExecutor(runtime, lambda kind, token: facts.append((kind, token))).execute(command()))
        self.assertEqual(facts, [("motion_rejected", "cmd")])

    def test_audio_only_fallback_does_not_call_the_motion_runtime(self) -> None:
        runtime = FakeRuntime(True)
        facts = []
        audio_only = PlaySegment("cmd", "turn", "segment", None, "a.wav", 1.0)
        self.assertFalse(PygameSharedSegmentExecutor(runtime, lambda kind, token: facts.append((kind, token))).execute(audio_only))
        self.assertEqual(runtime.calls, [])
        self.assertEqual(facts, [("motion_rejected", "cmd")])

    def test_scheduler_motion_is_executed_exactly(self) -> None:
        runtime = FakeRuntime(True)
        started, finished = [], []
        command = ScheduledMotion("IDLE_C", 1, 1, "timed_idle", "exp_smile01")
        self.assertTrue(PygameScheduledMotionExecutor(runtime).execute(command, lambda: started.append(True), lambda: finished.append(True)))
        self.assertEqual(runtime.calls, [("expression", "exp_smile01"), ("motion", "IDLE_C", 1, 1, None, False)])
        self.assertEqual((started, finished), ([True], [True]))

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
