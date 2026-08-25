from __future__ import annotations

import os
import sys
import unittest
from queue import Queue

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root not in sys.path:
    sys.path.insert(0, root)

from live2d_support.emotion_shadow_comparator import EmotionShadowComparator


class EmotionShadowComparatorTest(unittest.TestCase):
    def test_reports_semantic_equivalence_and_preserves_index_diagnostic(self):
        facts, commands, reports = Queue(), Queue(), []
        facts.put({"type":"baseline_emotion_motion","data":{"segment_id":"1","group":"happiness_C","index":0,"priority":3,"position":"C","expression_id":"exp_smile"}})
        commands.put({"type":"play_motion","data":{"turn_id":"legacy-shadow","segment_id":"1","group":"happiness_C","index":2,"priority":3,"position":"C","expression_id":"exp_smile"}})
        comparator = EmotionShadowComparator(facts, commands, reports.append)
        self.assertEqual(comparator.run_once(), 1)
        self.assertEqual(reports, [{"type":"emotion_shadow_comparison","data":{"segment_id":"1","equivalent":True,"differences":{},"baseline_index":0,"owner_index":2}}])

    def test_reports_a_stable_contract_drift(self):
        facts, commands, reports = Queue(), Queue(), []
        commands.put({"type":"play_motion","data":{"turn_id":"legacy-shadow","segment_id":"1","group":"happiness_C","index":0,"priority":3,"position":"C","expression_id":None}})
        facts.put({"type":"baseline_emotion_motion","data":{"segment_id":"1","group":"sadness_C","index":0,"priority":3,"position":"C","expression_id":None}})
        comparator = EmotionShadowComparator(facts, commands, reports.append)
        comparator.run_once()
        self.assertFalse(reports[0]["data"]["equivalent"])
        self.assertIn("group", reports[0]["data"]["differences"])


if __name__ == "__main__":
    unittest.main()
