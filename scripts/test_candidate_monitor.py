from __future__ import annotations

import datetime as dt
import unittest

from update_candidate_monitor import (
    KST,
    aggregate_30m,
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


if __name__ == "__main__":
    unittest.main()
