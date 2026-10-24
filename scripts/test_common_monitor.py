import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from common_monitor import load_ah_candidates, load_first_rise_candidates, merge_candidates
from image_pattern_search import process
from test_image_pattern_search import fixture, at


class CommonMonitorTests(unittest.TestCase):
    def test_native_only_mas_without_daily_estimated_prefix(self):
        start = dt.datetime.fromisoformat("2026-10-20T09:00+09:00")
        history = [{"t": (start+dt.timedelta(minutes=30*i)).isoformat(), "c": 100+i} for i in range(65)]
        candidate = {"code": "123456", "candidateTier": "ah", "tradingDaysRemaining": 3,
                     "displayCharts": {"intraday": {"source": "naver_native", "history": history,
                                                    "series": [{"m120": 999}], "ahModel": {"fake": True}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"ah.json"
            path.write_text(json.dumps({"candidates": [candidate]}), encoding="utf8")
            items = load_ah_candidates(path, start+dt.timedelta(days=3))
            chart = items[0]["displayCharts"]["intraday"]
            self.assertIsNone(chart["series"][58]["m60"])
            self.assertAlmostEqual(chart["series"][59]["m60"], 129.5)
            self.assertIsNone(chart["series"][-1]["m120"])
            self.assertNotIn("ahModel", chart)
            self.assertEqual(json.loads(path.read_text())["candidates"][0], candidate)

    def test_merge_deduplicates_and_honors_manual_exclusions(self):
        ag = {"code": "123456", "candidateTier": "core", "registeredAt": "old"}
        ah = {"code": "123456", "candidateTier": "ah", "registeredAt": "new"}
        extra = {"code": "654321", "candidateTier": "ah"}
        self.assertEqual(merge_candidates([ag], [ah, extra]), [ag, extra])
        self.assertEqual(merge_candidates([ag], [ah, extra], ["123456"]), [extra])
        self.assertEqual(ag["registeredAt"], "old")

    def test_ah_image_detection_notifications_and_dedup_match_ag(self):
        payload, rules, board = fixture()
        payload["candidates"][0]["candidateTier"] = "ah"
        sender = Mock()
        self.assertEqual(process(payload, at("11:00"), "test", False, board, rules, sender)[0], 0)
        self.assertEqual(process(payload, at("11:30"), "test", False, board, rules, sender)[0], 1)
        self.assertEqual(process(payload, at("12:00"), "test", False, board, rules, sender)[0], 1)
        self.assertEqual(process(payload, at("12:10"), "test", False, board, rules, sender)[0], 0)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(payload["imageSearch"]["candidateCount"], 1)
        self.assertEqual(payload["imageSearch"]["scope"], "active_ak_ag_ah_first_rise")

    def test_initial_ah_image_enable_never_replays_past_notifications(self):
        payload, rules, board = fixture()
        payload["candidates"][0]["candidateTier"] = "ah"
        sender = Mock()
        process(payload, at("12:10"), "test", False, board, rules, sender)
        self.assertTrue(payload["imageSearch"]["matches"])
        sender.assert_not_called()

    def test_dual_membership_does_not_double_send(self):
        payload, rules, board = fixture()
        ah = copy.deepcopy(payload["candidates"][0])
        ah["candidateTier"] = "ah"
        payload["candidates"] = merge_candidates(payload["candidates"], [ah])
        sender = Mock()
        process(payload, at("11:00"), "test", False, board, rules, sender)
        process(payload, at("11:30"), "test", False, board, rules, sender)
        self.assertEqual(sender.call_count, 1)


if __name__ == "__main__":
    unittest.main()
