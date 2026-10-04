from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

import update_candidate_monitor as monitor

from update_candidate_monitor import (
    KST,
    aggregate_30m,
    depth_rule_context,
    evaluate_ag,
    rise_context,
    valid_post_candidate_sequence,
)


class CandidateMonitorTests(unittest.TestCase):
    def test_ag_screen_matches_finalized_rules(self) -> None:
        rows = [
            {"date": f"2026-01-{index + 1:02d}", "close": 100, "volume": 100}
            for index in range(23)
        ]
        rows[19]["close"] = 100
        rows[20].update(close=104, volume=1000)
        rows[21].update(close=100, volume=200)
        rows[22].update(close=102, volume=300)
        result = evaluate_ag(rows, 22)
        self.assertIsNotNone(result)
        self.assertTrue(all(result["checks"].values()))

    def test_30m_aggregation_excludes_unfinished_bar(self) -> None:
        rows = [
            {
                "time": dt.datetime(2026, 10, 5, 9, minute, tzinfo=KST),
                "price": 100 + minute,
                "cumulativeVolume": 10 * (minute + 1),
            }
            for minute in range(50)
        ]
        current = dt.datetime(2026, 10, 5, 9, 45, tzinfo=KST)
        bars = aggregate_30m(rows, current)
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0]["open"], bars[0]["close"], bars[0]["volume"]), (100, 129, 300))

    def test_five_rises_require_reversal_under_ma40_and_prior_cross(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 1, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": 110 - index,
                "ma40": 80.0,
            }
            for index in range(50)
        ]
        bars[34]["ma20"] = 82
        bars[35]["ma20"] = 79
        bars[36]["ma20"] = 78
        for index, value in enumerate((77, 78, 79, 80, 81, 82), start=37):
            bars[index]["ma20"] = value
            bars[index]["ma40"] = 85
        context = rise_context(bars, 42)
        self.assertEqual(context["riseCount"], 5)
        self.assertEqual(context["reversalIndex"], 37)
        self.assertTrue(context["reversalUnderMa40"])
        self.assertTrue(context["priorDeathCross"])

    def test_sequence_requires_post_candidate_death_cross(self) -> None:
        signal_day = dt.date(2026, 10, 1)
        bars = [
            {
                "time": dt.datetime(2026, 10, 1, 9, tzinfo=KST) + dt.timedelta(hours=index),
                "ma20": value,
                "ma40": 100.0,
            }
            for index, value in enumerate((104, 103, 99, 98, 97, 98, 99, 100, 101, 102))
        ]
        context = rise_context(bars, 9)
        self.assertFalse(valid_post_candidate_sequence(bars, context, signal_day, True))
        for bar in bars:
            bar["time"] += dt.timedelta(days=1)
        self.assertTrue(valid_post_candidate_sequence(bars, context, signal_day, True))
        self.assertFalse(valid_post_candidate_sequence(bars, context, signal_day, False))

    def test_deep_pullback_waits_for_ma60_recovery(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": 110.0,
                "ma40": 100.0,
                "ma60": 95.0,
            }
            for index in range(20)
        ]
        bars[4]["ma20"] = 101
        bars[5]["ma20"] = 99
        for index, value in enumerate((94, 93, 94, 95, 96, 97, 98), start=6):
            bars[index]["ma20"] = value
        context = rise_context(bars, 12)
        depth = depth_rule_context(bars, context, 12, 90.0)
        self.assertTrue(depth["breachedMa60"])
        self.assertTrue(depth["recoveredMa60"])
        self.assertTrue(depth["signalReady"])
        bars[12]["ma20"] = 94.5
        context = rise_context(bars, 12)
        depth = depth_rule_context(bars, context, 12, 90.0)
        self.assertFalse(depth["recoveredMa60"])
        self.assertFalse(depth["signalReady"])

    def test_daily_ma10_breach_excludes_signal(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": 105.0,
                "ma40": 100.0,
                "ma60": 95.0,
            }
            for index in range(20)
        ]
        bars[4]["ma20"] = 101
        bars[5]["ma20"] = 99
        for index, value in enumerate((91, 89, 90, 92, 94, 96, 98), start=6):
            bars[index]["ma20"] = value
        context = rise_context(bars, 12)
        depth = depth_rule_context(bars, context, 12, 90.0)
        self.assertTrue(depth["breachedDailyMa10"])
        self.assertFalse(depth["signalReady"])

    def test_daily_ma10_breach_is_detected_without_baseline_history(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 9, 28, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "open": 99,
                "close": 99,
                "ma20": 99.0,
                "ma40": None,
                "ma60": None,
            }
            for index in range(20)
        ]
        candidate = {
            "code": "041830",
            "dailySignalDate": "2026-09-17",
            "dailyReference": {"ma10": 100.0},
            "tracking": {"breachedDailyMa10": True},
        }

        with mock.patch.object(monitor, "fetch_minute_rows", return_value=[]), mock.patch.object(
            monitor, "aggregate_30m", return_value=bars
        ):
            result = monitor.analyze_intraday(
                candidate,
                dt.datetime(2026, 10, 2, 16, tzinfo=KST),
                5000,
            )

        self.assertIsNone(result["baselineMa20AboveMa40"])
        self.assertTrue(result["breachedDailyMa10"])
        self.assertTrue(candidate["tracking"]["breachedDailyMa10"])

    def test_pre_signal_daily_ma10_breach_does_not_exclude_candidate(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 1, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "open": 99,
                "close": 99,
                "ma20": 99.0 if index < 10 else 105.0,
                "ma40": 100.0,
                "ma60": 95.0,
            }
            for index in range(26)
        ]
        candidate = {
            "code": "005720",
            "dailySignalDate": "2026-10-02",
            "dailyReference": {"ma10": 100.0},
            "tracking": {"breachedDailyMa10": True},
        }

        with mock.patch.object(monitor, "fetch_minute_rows", return_value=[]), mock.patch.object(
            monitor, "aggregate_30m", return_value=bars
        ):
            result = monitor.analyze_intraday(
                candidate,
                dt.datetime(2026, 10, 4, 16, tzinfo=KST),
                5000,
            )

        self.assertEqual(result["riseCount"], 0)
        self.assertFalse(result["breachedDailyMa10"])
        self.assertNotIn("breachedDailyMa10", candidate["tracking"])
        self.assertEqual(candidate["tracking"]["ruleVersion"], monitor.TRACKING_RULE_VERSION)

    def test_candidate_with_ma_values_waits_for_post_signal_sequence(self) -> None:
        candidate = {"code": "036200", "dailySignalDate": "2026-10-01"}
        payload = {"candidates": [candidate], "notifiedSignals": []}
        intraday = {
            "dataStatus": "ok",
            "baselineMa20AboveMa40": True,
            "ma20": 10794.5,
            "ma40": 10554.25,
            "ma60": 10275.83,
            "ma60Ready": False,
            "eligibleReversal": False,
            "breachedDailyMa10": False,
            "breachedMa60": False,
            "riseCount": 0,
        }

        with mock.patch.object(monitor, "analyze_intraday", return_value=intraday):
            monitor.enrich(
                payload,
                dt.datetime(2026, 10, 4, 16, tzinfo=KST),
                5000,
                True,
            )

        self.assertEqual(candidate["status"], "watching")


if __name__ == "__main__":
    unittest.main()
