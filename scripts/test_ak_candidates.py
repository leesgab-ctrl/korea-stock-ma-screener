import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import update_candidate_monitor as monitor


def qualifying_rows():
    start = dt.date(2026, 5, 6)
    rows = [{"date": (start + dt.timedelta(days=i)).isoformat(), "close": 2000 if i < 100 else 1000, "volume": 100} for i in range(160)]
    for offset, close in [(4, 1000), (3, 1100), (2, 1210), (1, 1331), (0, 1331 * .98)]:
        rows[-1 - offset]["close"] = close
    rows[-2]["volume"] = 400
    return rows


def relaxed_rows(day="2026-10-16", rise=9):
    rows = qualifying_rows()
    end = dt.date.fromisoformat(day)
    for i, row in enumerate(rows):
        row["date"] = (end - dt.timedelta(days=len(rows) - i - 1)).isoformat()
    for step in range(1, 4):
        rows[-5 + step]["close"] = 1000 * (1 + rise / 100) ** step
    rows[-1]["close"] = rows[-2]["close"] * .98
    return rows


class AkConditionsTest(unittest.TestCase):
    def evaluate(self, rows):
        return monitor.evaluate_ak(rows, len(rows) - 1)

    def test_all_conditions_and_corrected_k(self):
        rows = qualifying_rows()
        result = self.evaluate(rows)
        self.assertEqual(result["candidateTier"], "expanded_ak")
        self.assertEqual(result["screeningFamily"], "A-K")
        self.assertEqual(set(result["checks"]), set("ABCDGHIJK"))
        self.assertTrue(all(result["checks"].values()))
        for key in ("riseFourToThreePct", "riseThreeToTwoPct", "riseTwoToOnePct"):
            self.assertAlmostEqual(result["values"][key], 10)
        self.assertAlmostEqual(result["values"]["signalClosePct"], -2)
        self.assertEqual(result["preSpikeDate"], rows[-3]["date"])
        self.assertEqual(result["patternStartDate"], rows[-5]["date"])

    def test_relaxed_boundary_starts_on_selection_date_october_16(self):
        for day, threshold in [("2026-10-16", 9), ("2026-10-19", 9)]:
            rows = relaxed_rows(day)
            result = self.evaluate(rows)
            self.assertIsNotNone(result)
            self.assertEqual(result["riseThresholdPct"], threshold)
            self.assertTrue(all(result["checks"].values()))
        self.assertIsNone(self.evaluate(relaxed_rows("2026-10-15")))
        self.assertEqual(self.evaluate(relaxed_rows("2026-10-15", 10))["riseThresholdPct"], 10)

    def test_each_of_i_j_k_can_qualify_alone_at_nine_percent(self):
        for letter in "IJK":
            for rise, expected in [(9, True), (8.999, False)]:
                rows = relaxed_rows()
                values = {"I": [1000, 1090, 1090], "J": [1000, 1000, 1090], "K": [1090, 1090, 1090]}[letter]
                values = [1000 + (value - 1000) * rise / 9 for value in values]
                for row, close in zip(rows[-4:-1], values):
                    row["close"] = close
                rows[-1]["close"] = rows[-2]["close"] * .98
                rows[-44]["close"] = rows[-43]["close"] = 900
                result = self.evaluate(rows)
                if expected:
                    self.assertIsNotNone(result)
                    self.assertEqual([key for key in "IJK" if result["checks"][key]], [letter])
                else:
                    self.assertIsNone(result)

    def test_relaxation_does_not_change_other_ak_checks(self):
        rows = relaxed_rows()
        result = self.evaluate(rows)
        self.assertEqual(result["logic"], "A and B and C and D and G and H and (I or J or K)")
        rows[-1]["close"] = rows[-2]["close"] * .981
        self.assertIsNone(self.evaluate(rows), "The -2% pullback still applies")
        rows = relaxed_rows()
        rows[-2]["volume"] = 100
        self.assertIsNone(self.evaluate(rows), "Volume condition is unchanged")

    def test_k_is_four_to_three_not_a_duplicate_of_i(self):
        rows = qualifying_rows()
        rows[-4]["close"] = rows[-3]["close"] = rows[-2]["close"] = 1100
        rows[-1]["close"] = 1100 * .98
        result = self.evaluate(rows)
        self.assertTrue(result["checks"]["K"])
        self.assertFalse(result["checks"]["I"])
        self.assertFalse(result["checks"]["J"])

    def test_i_or_j_can_qualify_alone_at_the_exact_boundary(self):
        for letter in ("I", "J"):
            rows = qualifying_rows()
            rows[-4]["close"] = 1000
            rows[-3]["close"] = 1100 if letter == "I" else 1000
            rows[-2]["close"] = 1100
            rows[-1]["close"] = 1100 * .98
            rows[-44]["close"] = rows[-43]["close"] = 900
            result = self.evaluate(rows)
            self.assertEqual([key for key in "IJK" if result["checks"][key]], [letter])
            self.assertIn("(I or J or K)", result["logic"])
            rows[-3]["close"] = rows[-2]["close"] = 1099 if letter == "I" else 1000
            if letter == "J":
                rows[-2]["close"] = 1099
            rows[-1]["close"] = rows[-2]["close"] * .98
            self.assertIsNone(self.evaluate(rows))

    def test_c_requires_at_least_two_percent_decline(self):
        rows = qualifying_rows()
        rows[-1]["close"] = 1331 * .980001
        self.assertIsNone(self.evaluate(rows))

    def test_a_includes_the_spike_in_its_twenty_bar_average(self):
        rows = qualifying_rows()
        for row in rows[-21:-1]:
            row["volume"] = 17
        rows[-2]["volume"] = 57
        self.assertEqual(self.evaluate(rows)["values"]["volumeSpikePct"], 200)
        rows[-2]["volume"] = 56
        self.assertIsNone(self.evaluate(rows))

    def test_b_requires_three_actual_increases_ending_yesterday(self):
        rows = qualifying_rows()
        rows[-44]["close"] = 1100
        self.assertIsNone(self.evaluate(rows))

    def test_d_requires_a_widening_gap_not_just_ordering(self):
        rows = qualifying_rows()
        rows[-11]["close"] = 1500
        self.assertIsNone(self.evaluate(rows))

    def test_g_and_h_are_daily_average_comparisons(self):
        rows = qualifying_rows()
        for row in rows[:100]:
            row["close"] = 1200
        self.assertIsNone(self.evaluate(rows))
        rows = qualifying_rows()
        rows[-7]["close"] = 5000
        self.assertIsNone(self.evaluate(rows), "H must use six bars ago")

    def test_missing_and_invalid_data(self):
        self.assertIsNone(self.evaluate(qualifying_rows()[-119:]))
        for field, value, index in [("close", 0, -1), ("close", float("nan"), -60), ("volume", -1, -2), ("volume", float("inf"), -3)]:
            rows = qualifying_rows()
            rows[index][field] = value
            self.assertIsNone(self.evaluate(rows))


class AkRegistrationTest(unittest.TestCase):
    def build(self, rows, previous=None, today=None, halted=False, excluded=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            if excluded:
                (root / "data/candidate-exclusions.json").write_text(json.dumps({"excluded": [{"code": "900001"}]}), encoding="utf-8")
            file = root / "stocks.json"
            stock = {"c": "900001", "n": "AK fixture", "m": "KOSPI", "s": 0, "h": halted,
                     "p": [row["close"] for row in rows], "v": [row["volume"] for row in rows]}
            file.write_text(json.dumps({"dates": [row["date"] for row in rows], "stocks": [stock]}), encoding="utf-8")
            now = dt.datetime.fromisoformat(f"{today or rows[-1]['date']}T17:00:00+09:00")
            with patch.object(monitor, "ROOT", root), patch.object(monitor, "now_kst", return_value=now), patch.object(monitor, "fetch_daily_chart", return_value={"series": []}):
                return monitor.build_daily_candidates(file, copy.deepcopy(previous or {}))

    def append_days(self, rows, count=1):
        last = dt.date.fromisoformat(rows[-1]["date"])
        return rows + [{"date": (last + dt.timedelta(days=i)).isoformat(), "close": 1000, "volume": 100} for i in range(1, count + 1)]

    def test_new_ak_is_registered_with_the_existing_ten_day_window(self):
        result = self.build(qualifying_rows())
        self.assertEqual(len(result["candidates"]), 1)
        item = result["candidates"][0]
        self.assertEqual(item["candidateTier"], monitor.AK_TIER)
        self.assertEqual(item["tradingDaysRemaining"], 10)
        self.assertEqual(item["registrationPrice"], qualifying_rows()[-1]["close"])
        self.assertEqual(result["candidateScan"]["newRegistrations"][0]["candidateTier"], monitor.AK_TIER)
        repeated = self.build(qualifying_rows(), result)
        self.assertEqual(repeated["candidateScan"]["newRegistrations"], [])

    def test_new_nine_percent_ak_uses_same_registration_window(self):
        rows = relaxed_rows()
        result = self.build(rows)
        self.assertEqual(len(result["candidates"]), 1)
        item = result["candidates"][0]
        self.assertEqual(item["daily"]["riseThresholdPct"], 9)
        self.assertEqual(item["dailySignalDate"], "2026-10-16")
        self.assertEqual(item["tradingDaysRemaining"], 10)
        self.assertEqual(result["strategy"]["akRiseThresholdEffectiveFrom"], "2026-10-16")

    def test_nine_percent_does_not_create_pre_effective_registration(self):
        self.assertEqual(self.build(relaxed_rows("2026-10-15"))["candidates"], [])

    def test_retained_ak_keeps_baseline_charts_and_classification(self):
        rows = qualifying_rows()
        previous = self.build(rows)
        item = previous["candidates"][0]
        item["displayCharts"] = {"intraday": {"series": [{"t": "saved", "m3": 100}]}}
        result = self.build(self.append_days(rows), previous)
        self.assertEqual(len(result["candidates"]), 1)
        retained = result["candidates"][0]
        for key in ("registeredAt", "registrationPrice", "dailySignalDate", "displayCharts", "candidateTier"):
            self.assertEqual(retained[key], item[key])
        self.assertEqual(retained["tradingDaysRemaining"], 9)

    def test_only_today_can_create_ak_no_backfill_or_stale_data(self):
        rows = qualifying_rows()
        self.assertEqual(self.build(self.append_days(rows))["candidates"], [])
        tomorrow = self.append_days(rows)[-1]["date"]
        self.assertEqual(self.build(rows, today=tomorrow)["candidates"], [])

    def test_active_ag_is_not_replaced_by_today_ak(self):
        rows = qualifying_rows()
        old = {"code": "900001", "candidateTier": "core", "dailySignalDate": rows[-2]["date"],
               "registeredAt": rows[-2]["date"] + "T17:00:00+09:00", "registrationPrice": 1331}
        with patch.object(monitor, "evaluate_ag", side_effect=lambda data, index: {"candidateTier": "core", "close": 1331} if index == len(rows) - 2 else None):
            result = self.build(rows, {"candidates": [old]})
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["registeredAt"], old["registeredAt"])
        self.assertEqual(result["candidates"][0]["candidateTier"], "core")

    def test_ag_has_priority_when_both_evaluators_match(self):
        with patch.object(monitor, "evaluate_ag", return_value={"candidateTier": "core", "close": 1331}):
            result = self.build(qualifying_rows())
        self.assertEqual(result["candidates"][0]["candidateTier"], "core")

    def test_retired_expanded_ag_does_not_block_new_ak(self):
        rows = qualifying_rows()
        old = {"code": "900001", "name": "old expanded", "candidateTier": "expanded",
               "dailySignalDate": rows[-2]["date"], "registeredAt": rows[-2]["date"] + "T17:00:00+09:00",
               "registrationPrice": 1331}
        result = self.build(rows, {"candidates": [old]})
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["candidateTier"], monitor.AK_TIER)
        self.assertEqual(result["history"][0]["archiveReason"], "expanded_ag_disabled")
        self.assertEqual(result["history"][0]["registrationPrice"], old["registrationPrice"])

    def test_toggle_stops_new_search_but_keeps_existing_ak(self):
        rows = qualifying_rows()
        previous = self.build(rows)
        with patch.object(monitor, "AK_SEARCH_ENABLED", False):
            self.assertEqual(self.build(rows)["candidates"], [])
            self.assertEqual(self.build(self.append_days(rows), previous)["candidates"][0]["candidateTier"], monitor.AK_TIER)

    def test_existing_exclusions_halts_and_expiry_apply(self):
        rows = qualifying_rows()
        self.assertEqual(self.build(rows, halted=True)["candidates"], [])
        self.assertEqual(self.build(rows, excluded=True)["candidates"], [])
        previous = self.build(rows)
        result = self.build(self.append_days(rows, 10), previous)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["history"][0]["candidateTier"], monitor.AK_TIER)
        self.assertEqual(result["history"][0]["daily"]["checks"], previous["candidates"][0]["daily"]["checks"])


if __name__ == "__main__":
    unittest.main()
