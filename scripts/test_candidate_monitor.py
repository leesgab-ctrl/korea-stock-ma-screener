from __future__ import annotations

import datetime as dt
import unittest
import tempfile
import json
import runpy
import sys
import os
from pathlib import Path
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
    def test_quote_uses_latest_regular_tick_not_completed_bar_or_afterhours(self):
        current = dt.datetime(2026, 10, 6, 9, 22, tzinfo=KST)
        candidate = {"dailyChart": {"series": [{"d": "2026-10-02", "c": 1000}, {"d": "2026-10-06", "c": 1020}]}}
        rows = [{"time": current.replace(hour=h, minute=m), "price": p} for h,m,p in [(8,50,1100), (9,20,1010), (9,25,1030), (16,0,1200)]]
        quote = monitor.latest_session_quote(candidate, rows, current)
        self.assertEqual(quote["quotePrice"], 1010)
        self.assertEqual(quote["quotePreviousClose"], 1000)
        self.assertEqual(quote["quoteChangePct"], 1.0)

    def test_exclude_command_does_not_require_full_market_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            payload = {"candidates": [{"code": "188260", "name": "test", "dailySignalDate": "2026-10-01"}]}
            (root / "data/candidate-monitor.json").write_text(json.dumps(payload), encoding="utf-8")
            (root / "data/candidate-exclusions.json").write_text('{"excluded": []}', encoding="utf-8")
            script = monitor.ROOT / "scripts/manage_candidate_exclusions.py"
            with mock.patch.object(monitor, "ROOT", root), mock.patch.object(sys, "argv", [str(script), "--action", "exclude"]), mock.patch.dict(os.environ, {"CODE": "188260", "REASON": "shape"}):
                runpy.run_path(str(script), run_name="__main__")
            saved = json.loads((root / "data/candidate-monitor.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["candidates"], [])

    def test_manual_exclusion_removes_candidate_and_excludes_statistics(self):
        from manage_candidate_exclusions import manage
        candidate = {"code": "188260", "name": "세니젠", "dailySignalDate": "2026-10-01", "status": "watching",
                     "daily": {"close": 1000}, "dailyChart": {"series": [{"d": "2026-10-02", "h": 1100, "c": 1090}]}}
        payload = {"candidates": [candidate], "history": []}
        registry = {"excluded": []}
        current = dt.datetime(2026, 10, 6, 15, tzinfo=KST)
        manage(payload, registry, "exclude", "188260", "shape", current)
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["validationSummary"]["totalDetected"], 1)
        self.assertEqual(payload["validationSummary"]["evaluated"], 0)
        self.assertEqual(payload["validationSummary"]["manualExcluded"], 1)
        self.assertEqual(payload["history"][0]["excludeReason"], "shape")
        manage(payload, registry, "restore", "188260", "", current, ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["tradingDaysRemaining"], 7)
        self.assertEqual(registry["excluded"], [])

    def test_excluded_candidate_is_not_analyzed_or_notified(self):
        candidate = {"code": "188260", "name": "세니젠", "dailySignalDate": "2026-10-01"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            (root / "data/candidate-exclusions.json").write_text(json.dumps({"excluded": [{"code": "188260", "name": "test"}]}))
            with mock.patch.object(monitor, "ROOT", root), mock.patch.object(monitor, "analyze_intraday") as analyze, mock.patch.object(monitor, "fetch_daily_chart") as daily, mock.patch.object(monitor, "notify_ntfy") as notify:
                payload = {"candidates": [candidate]}
                monitor.enrich(payload, dt.datetime(2026, 10, 6, 15, tzinfo=KST), 5000, False, True)
                analyze.assert_not_called()
                daily.assert_not_called()
                notify.assert_not_called()
                self.assertEqual(payload["summary"]["active"], 0)

    def test_restore_does_not_reactivate_expired_candidate(self):
        from manage_candidate_exclusions import manage
        candidate = {"code": "188260", "dailySignalDate": "2026-09-01"}
        payload = {"candidates": [], "history": []}
        registry = {"excluded": [{"code": "188260", "candidate": candidate}]}
        calendar = [f"2026-09-{d:02d}" for d in range(1, 15)]
        manage(payload, registry, "restore", "188260", "", dt.datetime(2026, 10, 6, tzinfo=KST), calendar)
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(registry["excluded"], [])

    def test_session_recovery_uses_boundary_prices_and_1500_cutoff(self):
        day = dt.datetime(2026, 10, 6, tzinfo=KST)
        candidate = {"dailySignalDate": "2026-10-05", "dailyChart": {"series": [{"d": "2026-10-05", "c": 1000}, {"d": "2026-10-06", "o": 1000}]}}
        bars = [{"time": day.replace(hour=9) + dt.timedelta(minutes=30*i),
                 "ma20": 1020+i, "ma40": 1010, "ma60": 1000} for i in range(12)]
        bars.insert(0, {"time": (day - dt.timedelta(days=1)).replace(hour=15), "ma20": 1019, "ma40": 1010, "ma60": 1000})
        rows = [{"time": day.replace(hour=h, minute=m), "price": p}
                for h,m,p in [(9,5,1050), (11,0,1005), (15,0,1010), (15,5,1100)]]
        result = monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))
        self.assertTrue(result["matched"])
        self.assertEqual(result["price"], 1010)
        self.assertEqual(result["openChangePct"], 1.0)
        rows[2]["price"] = 1005
        self.assertTrue(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        for price in (1004, 1011):
            rows[2]["price"] = price
            self.assertFalse(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        rows[2]["price"] = 1010
        candidate["dailyChart"]["series"][-1]["o"] = 990
        self.assertFalse(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        candidate["dailyChart"]["series"][-1]["o"] = 1000
        rows[1]["price"] = 1006
        self.assertFalse(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        rows[1]["price"] = 1005
        rows[0]["time"] = day.replace(hour=9, minute=6)
        self.assertFalse(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["matched"])
        rows.pop(2)
        self.assertEqual(monitor.evaluate_session_recovery(candidate, rows, bars, day.replace(hour=15, minute=10))["dataStatus"], "insufficient")

    def test_candidate_outcome_tracks_five_percent_and_final_return(self) -> None:
        candidate = {
            "registeredAt": "2026-10-01T16:00:00+09:00", "registrationPrice": 1000,
            "code": "005720",
            "name": "넥센",
            "dailySignalDate": "2026-10-01",
            "daily": {"close": 1000, "spikeDate": "2026-09-29", "values": {}},
            "dailyChart": {
                "series": [
                    {"d": "2026-09-30", "h": 990, "c": 980},
                    {"d": "2026-10-01", "h": 1200, "c": 1000},
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

    def test_same_ag_day_high_is_not_a_forward_success(self) -> None:
        candidate = {
            "registeredAt": "2026-10-01T16:00:00+09:00", "registrationPrice": 1000,
            "dailySignalDate": "2026-10-01",
            "daily": {"close": 1000},
            "dailyChart": {"series": [
                {"d": "2026-10-01", "h": 1200, "c": 1000},
                {"d": "2026-10-02", "h": 1040, "c": 1020},
                {"d": "2026-10-05", "h": 1100, "c": 1080},
            ]},
            "outcomeEndDate": "2026-10-02",
        }
        outcome = candidate_outcome(candidate)
        self.assertFalse(outcome["reached5Pct"])
        self.assertEqual(outcome["peakReturnPct"], 4.0)
        self.assertEqual(outcome["finalReturnPct"], 2.0)
        candidate["dailyChart"]["series"] = candidate["dailyChart"]["series"][:1]
        self.assertEqual(candidate_outcome(candidate)["dataStatus"], "insufficient")

    def test_history_migration_recalculates_only_until_archive_date(self) -> None:
        record = {
            "registeredAt": "2026-10-01T16:00:00+09:00", "registrationPrice": 1000,
            "code": "005720", "dailySignalDate": "2026-10-01",
            "agClose": 1000, "archivedAt": "2026-10-02",
            "outcome": {"dataStatus": "ok", "reached5Pct": True, "reached5PctDate": "2026-10-01"},
        }
        chart = {"series": [
            {"d": "2026-10-01", "h": 1200, "c": 1000},
            {"d": "2026-10-02", "h": 1040, "c": 1020},
            {"d": "2026-10-05", "h": 1100, "c": 1080},
        ]}
        with mock.patch.object(monitor, "fetch_daily_chart", return_value=chart) as fetch:
            monitor.refresh_history_outcomes({"history": [record]})
            monitor.refresh_history_outcomes({"history": [record]})
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(record["outcome"]["reached5Pct"])
        self.assertEqual(record["outcome"]["finalDate"], "2026-10-02")

    def test_registration_replaces_old_ag_price_and_excludes_earlier_highs(self) -> None:
        candidate = {
            "dailySignalDate": "2026-09-28", "daily": {"close": 1000},
            "registeredAt": "2026-10-04T12:00:00+09:00", "registrationPrice": 1200,
            "dailyChart": {"series": [
                {"d": "2026-10-02", "h": 1500, "c": 1200},
                {"d": "2026-10-05", "h": 1250, "c": 1210},
                {"d": "2026-10-06", "h": 1265, "c": 1230},
            ]},
        }
        outcome = candidate_outcome(candidate)
        self.assertEqual(outcome["startPrice"], 1200)
        self.assertEqual(outcome["targetPrice"], 1260)
        self.assertEqual(outcome["reached5PctDate"], "2026-10-06")
        self.assertEqual(outcome["reached5PctTradingDays"], 2)
        candidate.pop("registeredAt")
        self.assertEqual(candidate_outcome(candidate)["dataStatus"], "insufficient")

    def test_registration_day_uses_only_later_complete_bars(self) -> None:
        candidate = {
            "registeredAt": "2026-10-06T10:15:00+09:00", "registrationPrice": 1000,
            "dailyChart": {"series": [{"d": "2026-10-06", "h": 1300, "c": 1020}]},
            "intraday": {"series": [
                {"t": "2026-10-06T10:00:00+09:00", "h": 1300, "c": 1000},
                {"t": "2026-10-06T10:30:00+09:00", "h": 1040, "c": 1020},
            ]},
        }
        self.assertFalse(candidate_outcome(candidate)["reached5Pct"])
        candidate["intraday"]["series"][-1]["h"] = 1060
        outcome = candidate_outcome(candidate)
        self.assertTrue(outcome["reached5Pct"])
        self.assertEqual(outcome["reached5PctTradingDays"], 0)

    def test_history_migration_failure_removes_stale_success_and_retries(self) -> None:
        record = {"code": "005720", "outcome": {"dataStatus": "ok", "reached5Pct": True}}
        with mock.patch.object(monitor, "fetch_daily_chart", side_effect=OSError("offline")) as fetch:
            monitor.refresh_history_outcomes({"history": [record]})
            monitor.refresh_history_outcomes({"history": [record]})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(record["outcome"]["dataStatus"], "error")
        self.assertFalse(record["outcome"]["reached5Pct"])

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
