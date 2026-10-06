from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

import update_candidate_monitor as monitor

from update_candidate_monitor import (
    KST,
    aggregate_30m,
    archive_candidate,
    candidate_outcome,
    consecutive_ma20_falls,
    depth_rule_context,
    daily_ma10_for_bar,
    evaluate_ag,
    fetch_daily_chart,
    apply_inferred_cross,
    infer_missing_baseline,
    merge_30m_history,
    rise_context,
    target_completed_before,
    update_validation_summary,
    valid_post_candidate_sequence,
)


class CandidateMonitorTests(unittest.TestCase):
    def test_session_recovery_uses_boundary_prices_and_1500_cutoff(self):
        day = dt.datetime(2026, 10, 6, tzinfo=KST)
        candidate = {"dailySignalDate": "2026-10-05", "dailyChart": {"series": [{"d": "2026-10-05", "c": 1000}]}}
        bars = [{"time": day.replace(hour=9) + dt.timedelta(minutes=30*i),
                 "ma20": 1020+i, "ma40": 1010, "ma60": 1000} for i in range(12)]
        bars.insert(0, {"time": (day - dt.timedelta(days=1)).replace(hour=15), "ma20": 1019, "ma40": 1010, "ma60": 1000})
        rows = [{"time": day.replace(hour=h, minute=m), "price": p}
                for h,m,p in [(9,5,1050), (11,0,995), (15,0,1010), (15,5,1100)]]
        result = monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))
        self.assertTrue(result["matched"])
        self.assertEqual(result["price"], 1010)
        rows[0]["time"] = day.replace(hour=9, minute=6)
        self.assertFalse(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        rows.pop(2)
        self.assertEqual(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["dataStatus"], "insufficient")

    def test_candidate_outcome_tracks_five_percent_and_final_return(self) -> None:
        candidate = {
            "code": "005720",
            "name": "넥센",
            "dailySignalDate": "2026-10-01",
            "daily": {"close": 1000, "spikeDate": "2026-09-29", "values": {}},
            "dailyChart": {
                "series": [
                    {"d": "2026-09-30", "h": 990, "c": 980},
                    {"d": "2026-10-01", "h": 1020, "c": 1000},
                    {"d": "2026-10-02", "h": 1060, "c": 1030},
                    {"d": "2026-10-05", "h": 1040, "c": 1010},
                ]
            },
        }

        outcome = candidate_outcome(candidate)
        archived = archive_candidate(candidate, "2026-10-15", "window_completed")

        self.assertTrue(outcome["reached5Pct"])
        self.assertEqual(outcome["reached5PctDate"], "2026-10-02")
        self.assertEqual(outcome["reached5PctTradingDays"], 1)
        self.assertEqual(outcome["peakReturnPct"], 6.0)
        self.assertEqual(outcome["finalReturnPct"], 1.0)
        self.assertEqual(archived["outcome"], outcome)

    def test_validation_summary_combines_active_and_archived_candidates(self) -> None:
        payload = {
            "candidates": [
                {
                    "code": "005720",
                    "dailySignalDate": "2026-10-01",
                    "outcome": {"dataStatus": "ok", "reached5Pct": True},
                }
            ],
            "history": [
                {
                    "id": "001250|2026-09-18",
                    "outcome": {"dataStatus": "ok", "reached5Pct": False},
                }
            ],
        }

        update_validation_summary(payload)

        self.assertEqual(payload["validationSummary"]["totalDetected"], 2)
        self.assertEqual(payload["validationSummary"]["reached5Pct"], 1)
        self.assertEqual(payload["validationSummary"]["reached5PctRate"], 50.0)

    def test_signaled_target_winner_moves_on_following_trading_day(self) -> None:
        candidate = {
            "intraday": {"signalTime": "2026-10-06T11:00:00+09:00"},
            "outcome": {"reached5Pct": True, "reached5PctDate": "2026-10-06"},
        }

        self.assertFalse(target_completed_before(candidate, "2026-10-06"))
        self.assertTrue(target_completed_before(candidate, "2026-10-07"))

    def test_target_winner_waits_until_day_after_later_signal(self) -> None:
        candidate = {
            "intraday": {"signalTime": "2026-10-07T10:30:00+09:00"},
            "outcome": {"reached5Pct": True, "reached5PctDate": "2026-10-06"},
        }

        self.assertFalse(target_completed_before(candidate, "2026-10-07"))
        self.assertTrue(target_completed_before(candidate, "2026-10-08"))

    def test_daily_chart_parses_candles_and_moving_averages(self) -> None:
        items = "".join(
            f'<item data="202601{index + 1:02d}|{100 + index}|{103 + index}|{99 + index}|{102 + index}|{1000 + index}" />'
            for index in range(20)
        )
        with mock.patch.object(monitor, "fetch_bytes", return_value=items.encode("euc-kr")):
            chart = fetch_daily_chart("005720", 90)

        self.assertEqual(chart["dataStatus"], "ok")
        self.assertEqual(len(chart["series"]), 20)
        self.assertEqual(chart["series"][-1]["c"], 121)
        self.assertIsNotNone(chart["series"][-1]["m5"])
        self.assertIsNotNone(chart["series"][-1]["m10"])
        self.assertIsNotNone(chart["series"][-1]["m20"])
        self.assertIsNone(chart["series"][-1]["m60"])

    def test_daily_chart_uses_warmup_rows_so_ma60_starts_visible(self) -> None:
        start = dt.date(2026, 1, 1)
        items = "".join(
            f'<item data="{(start + dt.timedelta(days=index)).strftime("%Y%m%d")}|{100 + index}|{103 + index}|{99 + index}|{102 + index}|{1000 + index}" />'
            for index in range(130)
        )
        with mock.patch.object(monitor, "fetch_bytes", return_value=items.encode("euc-kr")):
            chart = fetch_daily_chart("005720")

        self.assertEqual(len(chart["series"]), 60)
        self.assertIsNotNone(chart["series"][0]["m60"])

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
        self.assertEqual(result["candidateTier"], "core")

    def test_ag_expanded_tier_only_relaxes_volume_and_signal_gain(self) -> None:
        rows = [
            {"date": f"2026-01-{index + 1:02d}", "close": 100, "volume": 100}
            for index in range(23)
        ]
        rows[20].update(close=104, volume=300)
        rows[21].update(close=100, volume=200)
        rows[22].update(close=100.6, volume=300)

        result = evaluate_ag(rows, 22)

        self.assertIsNotNone(result)
        self.assertEqual(result["candidateTier"], "expanded")
        self.assertFalse(result["coreChecks"]["A"])
        self.assertFalse(result["coreChecks"]["E"])
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

    def test_saved_30m_history_is_merged_and_mas_are_recalculated(self) -> None:
        start = dt.datetime(2026, 9, 21, 9, tzinfo=KST)

        def row(index: int, close: int) -> dict:
            return {
                "time": start + dt.timedelta(minutes=30 * index),
                "open": close, "high": close + 1, "low": close - 1,
                "close": close, "volume": index + 1,
            }

        old_bars = [row(index, 100 + index) for index in range(70)]
        candidate = {
            "intradayHistory": {
                "series": [
                    {
                        "t": bar["time"].isoformat(timespec="minutes"),
                        "o": bar["open"], "h": bar["high"], "l": bar["low"],
                        "c": bar["close"], "v": bar["volume"],
                    }
                    for bar in old_bars
                ]
            }
        }
        fresh = [row(index, 1000 + index) for index in range(65, 83)]

        merged = merge_30m_history(candidate, fresh)

        self.assertEqual(len(merged), 83)
        self.assertEqual(merged[65]["close"], 1065)
        self.assertIsNotNone(merged[59]["ma60"])
        self.assertEqual(len(candidate["intradayHistory"]["series"]), 83)

    def test_latest_trading_days_keeps_exactly_five_distinct_days(self) -> None:
        bars = []
        for day_offset in range(7):
            day = dt.date(2026, 9, 21) + dt.timedelta(days=day_offset)
            for hour in (9, 10):
                bars.append({"time": dt.datetime.combine(day, dt.time(hour), KST)})

        selected = monitor.latest_trading_days(bars, 5)

        self.assertEqual(len(selected), 10)
        self.assertEqual(selected[0]["time"].date(), dt.date(2026, 9, 23))
        self.assertEqual(selected[-1]["time"].date(), dt.date(2026, 9, 27))

    def test_latest_trading_days_keeps_available_days_when_under_five(self) -> None:
        bars = [
            {"time": dt.datetime(2026, 10, day, 9, tzinfo=KST)}
            for day in (1, 2, 5)
        ]

        self.assertEqual(monitor.latest_trading_days(bars, 5), bars)

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

    def test_missing_baseline_can_recover_a_legacy_below_state(self) -> None:
        signal_day = dt.date(2026, 9, 28)
        bars = [
            {
                "time": dt.datetime(2026, 9, 30, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": value,
                "ma40": 100.0,
                "ma60": 95.0,
            }
            for index, value in enumerate((99, 97, 94, 93, 94, 95, 96, 97, 98, 99))
        ]
        inferred, synthetic_cross = infer_missing_baseline(bars, signal_day)
        context = apply_inferred_cross(rise_context(bars, 9), synthetic_cross)

        self.assertTrue(inferred)
        self.assertEqual(synthetic_cross, 0)
        self.assertTrue(context["priorDeathCross"])
        self.assertTrue(context["deathCrossInferred"])
        self.assertTrue(valid_post_candidate_sequence(bars, context, signal_day, inferred))

    def test_shallow_pullback_uses_ma3_ma40_target(self) -> None:
        values = (99, 97, 95, 94, 95, 96, 97, 98, 99, 100, 101, 102)
        bars = [
            {
                "time": dt.datetime(2026, 9, 30, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "open": value, "high": value, "low": value, "close": value, "volume": 1,
                "ma20": float(value),
                "ma40": 105.0 if index < 11 else 98.0,
                "ma60": None if index < 11 else 99.0,
            }
            for index, value in enumerate(values)
        ]
        candidate = {"code": "289930", "dailySignalDate": "2026-09-17", "dailyReference": {}}

        with mock.patch.object(monitor, "fetch_minute_rows", return_value=[]), mock.patch.object(
            monitor, "aggregate_30m", return_value=bars
        ):
            result = monitor.analyze_intraday(
                candidate, dt.datetime(2026, 10, 1, 16, tzinfo=KST), 5000
            )

        self.assertEqual(result["signalTime"], "2026-09-30T14:30+09:00")
        self.assertEqual(result["signalRule"], "ma3_ma40")
        self.assertEqual(result["signalTarget"], "MA40")

    def test_deep_pullback_waits_for_ma60_recovery(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": 110.0,
                "ma3": 110.0,
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
        bars[12]["ma3"] = 96.0
        depth = depth_rule_context(bars, context, 12, 90.0)
        self.assertTrue(depth["breachedMa60"])
        self.assertEqual(depth["entryTarget"], "MA60")
        self.assertTrue(depth["ma3AboveTarget"])
        self.assertTrue(depth["signalReady"])
        bars[12]["ma3"] = 94.5
        context = rise_context(bars, 12)
        depth = depth_rule_context(bars, context, 12, 100.0)
        self.assertFalse(depth["ma3AboveTarget"])
        self.assertFalse(depth["signalReady"])

    def test_ma60_depth_uses_available_comparable_bars(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": value,
                "ma3": value,
                "ma40": 100.0,
                "ma60": None if index < 6 else 95.0,
            }
            for index, value in enumerate((105, 103, 99, 97, 94, 93, 92, 93, 94, 95, 96, 97))
        ]
        context = rise_context(bars, 11)
        depth = depth_rule_context(bars, context, 11, 90.0)

        self.assertTrue(depth["ma60Ready"])
        self.assertTrue(depth["breachedMa60"])
        self.assertEqual(depth["entryTarget"], "MA60")
        self.assertTrue(depth["ma3AboveTarget"])
        self.assertTrue(depth["signalReady"])

    def test_daily_ma10_breach_does_not_block_intraday_signal(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": 105.0,
                "ma3": 105.0,
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
        depth = depth_rule_context(bars, context, 12, 100.0)
        self.assertTrue(depth["breachedDailyMa10"])
        self.assertTrue(depth["signalReady"])

    def test_daily_ma10_recovery_allows_a_later_signal(self) -> None:
        bars = [
            {
                "time": dt.datetime(2026, 10, 2, 9, tzinfo=KST) + dt.timedelta(minutes=30 * index),
                "ma20": value,
                "ma3": value,
                "ma40": 100.0,
                "ma60": 95.0,
            }
            for index, value in enumerate((105, 101, 99, 94, 93, 94, 95, 96, 97, 98))
        ]
        context = rise_context(bars, 9)

        self.assertTrue(depth_rule_context(bars, context, 4, 96.0)["breachedDailyMa10"])
        recovered = depth_rule_context(bars, context, 9, 96.0)
        self.assertFalse(recovered["breachedDailyMa10"])
        self.assertTrue(recovered["signalReady"])

    def test_ma60_wait_is_used_even_below_daily_ma10(self) -> None:
        candidate = {"code": "215790", "dailySignalDate": "2026-09-18"}
        payload = {"candidates": [candidate], "notifiedSignals": []}
        intraday = {
            "dataStatus": "ok", "baselineMa20AboveMa40": True,
            "ma20": 654.6, "ma40": 654.17, "ma60": 657.83,
            "breachedMa60": True, "entryTarget": "MA60", "ma3AboveTarget": False,
            "breachedDailyMa10": True, "structuralExcluded": False,
            "riseCount": 13,
        }

        with mock.patch.object(monitor, "analyze_intraday", return_value=intraday):
            monitor.enrich(payload, dt.datetime(2026, 10, 5, 16, tzinfo=KST), 5000, True)

        self.assertEqual(candidate["status"], "waiting60")

    def test_structural_decline_counts_consecutive_ma20_falls(self) -> None:
        bars = [{"ma20": value} for value in (110.0, 109.0, 108.0, 107.0, 106.0)]
        self.assertEqual(consecutive_ma20_falls(bars, 4), 4)
        bars[3]["ma20"] = 106.0
        self.assertEqual(consecutive_ma20_falls(bars, 4), 0)

    def test_structural_decline_is_excluded_but_simple_ma10_breach_is_watched(self) -> None:
        candidates = [
            {"code": "357880", "dailySignalDate": "2026-09-21"},
            {"code": "215790", "dailySignalDate": "2026-09-21"},
        ]
        payload = {"candidates": candidates, "notifiedSignals": []}
        structural = {
            "dataStatus": "ok", "baselineMa20AboveMa40": True,
            "ma20": 90.0, "ma40": 95.0, "ma60": 100.0,
            "breachedDailyMa10": True, "structuralExcluded": True,
        }
        recoverable = {
            **structural, "ma20": 96.0, "ma40": 95.0,
            "structuralExcluded": False,
        }

        with mock.patch.object(monitor, "analyze_intraday", side_effect=(structural, recoverable)):
            monitor.enrich(payload, dt.datetime(2026, 10, 4, 16, tzinfo=KST), 5000, True)

        self.assertEqual(candidates[0]["status"], "excluded")
        self.assertEqual(candidates[1]["status"], "watching")
        self.assertEqual(payload["summary"]["active"], 1)

    def test_daily_ma10_uses_the_previous_completed_daily_value_for_each_date(self) -> None:
        reference = {
            "date": "2026-10-02",
            "ma10": 105.0,
            "ma10PreviousByDate": {
                "2026-10-01": 90.0,
                "2026-10-02": 95.0,
            },
        }
        bar_october_1 = {"time": dt.datetime(2026, 10, 1, 15, tzinfo=KST)}
        bar_october_2 = {"time": dt.datetime(2026, 10, 2, 15, tzinfo=KST)}
        next_session = {"time": dt.datetime(2026, 10, 5, 9, tzinfo=KST)}

        self.assertEqual(daily_ma10_for_bar(reference, bar_october_1), 90.0)
        self.assertEqual(daily_ma10_for_bar(reference, bar_october_2), 95.0)
        self.assertEqual(daily_ma10_for_bar(reference, next_session), 105.0)

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
        self.assertNotIn("breachedDailyMa10", candidate["tracking"])

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

    def test_crossed_candidate_with_zero_rises_is_buy_setup(self) -> None:
        candidate = {"code": "014280", "dailySignalDate": "2026-10-01"}
        payload = {"candidates": [candidate], "notifiedSignals": []}
        intraday = {
            "dataStatus": "ok",
            "baselineMa20AboveMa40": True,
            "ma20": 4632.0,
            "ma40": 4719.5,
            "ma60": 4759.0,
            "ma60Ready": True,
            "eligibleReversal": True,
            "reversalUnderMa40": True,
            "priorDeathCross": True,
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

        self.assertEqual(candidate["status"], "setup")
        self.assertEqual(payload["summary"]["setup"], 1)


if __name__ == "__main__":
    unittest.main()
