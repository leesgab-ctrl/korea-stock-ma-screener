import copy
import datetime as dt
import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
import image_pattern_search as search


class PyramidReviewTests(unittest.TestCase):
    def setUp(self):
        rules = json.loads(search.RULES_PATH.read_text(encoding="utf-8"))["rules"]
        self.rule = next(rule for rule in rules if rule["type"] == "ma3_ma60_near_rebound")
        self.legacy = next(rule for rule in rules if rule["type"] == "ma20_pyramid_ma60_recovery")
        start = search.timestamp("2026-10-15T09:00+09:00")
        m20 = [100, 101, 102, 103, 104, 105, 106, 105, 104, 103, 102, 101, 100, 99, 98, 98, 98]
        m3 = [102] * 9 + [99.5, 99.3, 99.1, 98.9, 99.4, 100.8, 101, 102]
        self.rows = [{"t": "2026-10-14T19:30+09:00", "m3": 102, "m20": 100, "m60": 99.95, "c": 102, "complete": True}]
        self.rows += [{"t": (start + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                       "m3": m3[i], "m20": value, "m60": 100 + .05 * i, "c": 103, "complete": True}
                      for i, value in enumerate(m20)]
        self.candidate = {"code": "000004", "name": "Pyramid", "candidateTier": "core",
                          "registeredAt": "2026-10-14T09:00+09:00", "displayCharts": {
                              "intraday": {"dataStatus": "ok", "series": self.rows}, "daily": {"history": []},
                              "quote": {"price": 103, "previousClose": 100, "time": "2026-10-15T19:30+09:00"}}}
        self.board = {"version": 1, "records": [{"id": self.rule["imageIds"][0], "conditionConfirmed": True,
                      "conditionConfirmedAt": "2026-10-14T09:00+09:00"}]}
        self.payload, self.sender = {"candidates": [self.candidate]}, Mock()

    def bar(self, index):
        return self.rows[index + 1]

    def detect(self):
        return search.detect(self.rows, self.rule)

    def run_at(self, clock, no_notify=False):
        return search.process(self.payload, search.timestamp("2026-10-15T" + clock + "+09:00"), "test-only", no_notify,
                              board=self.board, rules=[self.rule], sender=self.sender)[0]

    def test_fixed_ma20_gate_keeps_original_numbers_and_image_binding(self):
        for key in ("minLegBars", "maxLegBars", "minRisePercent", "minRiseDayPercent", "strongRiseDayPercent",
                    "minFallPercent", "minFallSpeedRatio", "minDirectionalRatio", "trendComparisons", "confirmationMaxBars"):
            self.assertEqual(self.rule[key], self.legacy[key], key)
        self.assertEqual(self.rule["maxGapPercent"], 2.5)
        self.assertEqual(self.rule["watchBelowBars"], 3)
        self.assertEqual(self.rule["imageIds"], ["7167e60c-b018-4cf6-9ccf-c3d40be2a815"])
        self.assertFalse(self.legacy["enabled"])

    def test_full_contract_is_review_then_cross_not_a_turn_alert(self):
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["watch", "confirmation"])
        self.assertEqual(events[0]["completedAt"], "2026-10-15T15:00+09:00")
        self.assertEqual(events[1]["completedAt"], "2026-10-15T16:30+09:00")
        self.assertEqual(events[0]["episodeTime"], events[1]["episodeTime"])
        self.assertTrue(all(event["pyramid"]["riseDayPercent"] >= 3 and event["pyramid"]["fallPercent"] >= 2 for event in events))
        self.assertTrue(all(event["stage"] != "primary" for event in events))

    def test_three_descending_ma3_bars_are_review_without_requiring_turn(self):
        review = self.detect()[0]
        self.assertEqual(len(review["watchBars"]), 3)
        self.assertTrue(all(a["m3"] > b["m3"] for a, b in zip(review["watchBars"], review["watchBars"][1:])))
        self.assertTrue(all(-2.5 <= row["gapPercent"] < 0 for row in review["watchBars"]))

    def test_only_two_below_bars_then_cross_do_not_create_review_or_confirmation(self):
        self.rows[:] = self.rows[:13]
        self.bar(11)["m3"] = self.bar(11)["m60"] + 1
        self.assertEqual(self.detect(), [])

    def test_exact_two_point_five_percent_is_allowed(self):
        for i in (9, 10, 11):
            self.bar(i)["m3"] = self.bar(i)["m60"] * .975
        self.assertEqual(self.detect()[0]["time"], self.bar(11)["t"])

    def test_outside_band_restarts_three_bar_count(self):
        self.bar(10)["m3"] = self.bar(10)["m60"] * .97499
        self.assertEqual(self.detect()[0]["time"], self.bar(13)["t"])

    def test_touching_ma60_is_not_below_and_restarts_count(self):
        self.bar(10)["m3"] = self.bar(10)["m60"]
        self.assertEqual(self.detect()[0]["time"], self.bar(13)["t"])

    def test_already_above_near_bounces_never_qualify(self):
        for row in self.rows:
            row["m3"] = row["m60"] + 1
        self.assertEqual(self.detect(), [])

    def test_initially_below_without_observed_descent_does_not_qualify(self):
        for row in self.rows:
            row["m3"] = row["m60"] - 1
        self.assertEqual(self.detect(), [])

    def test_flat_ma20_near_below_three_bars_is_not_pyramid(self):
        for row in self.rows:
            row["m20"] = 100
        self.assertEqual(self.detect(), [])

    def test_only_rising_or_only_falling_ma20_is_not_pyramid(self):
        for direction in (-1, 1):
            for i, row in enumerate(self.rows):
                row["m20"] = 100 + direction * .2 * i
            self.assertEqual(self.detect(), [])

    def test_shallow_ma20_fall_is_excluded(self):
        for row in self.rows[8:]:
            row["m20"] = 105.9
        self.assertEqual(self.detect(), [])

    def test_insufficient_ma20_daily_rise_is_excluded(self):
        self.rows[0]["m20"] = 106
        self.assertEqual(self.detect(), [])

    def test_falling_ma60_is_excluded(self):
        for i, row in enumerate(self.rows):
            row["m60"] = 102 - .01 * i
        self.assertEqual(self.detect(), [])

    def test_ma60_warmup_does_not_erase_ma20_rise(self):
        expected = self.detect()
        for row in self.rows[:5]:
            row["m60"] = None
        self.assertEqual(self.detect(), expected)

    def test_missing_ma20_or_regular_bar_cannot_bypass_gate(self):
        self.bar(6)["m20"] = None
        self.assertEqual(self.detect(), [])
        self.setUp()
        del self.rows[5]
        self.assertEqual(self.detect(), [])

    def test_future_pyramid_cannot_create_earlier_review(self):
        self.assertEqual(search.detect(self.rows[:8], self.rule), [])
        before = search.detect(self.rows[:13], self.rule)
        after = [event for event in self.detect() if event["time"] <= self.rows[12]["t"]]
        self.assertEqual(before, after)

    def test_small_ma3_decline_after_review_does_not_cancel_confirmation(self):
        self.assertLess(self.bar(12)["m3"], self.bar(11)["m3"])
        self.assertEqual([event["stage"] for event in self.detect()], ["watch", "confirmation"])

    def test_ma20_rebound_after_review_does_not_erase_accepted_pyramid(self):
        for i, value in [(12, 102), (13, 103), (14, 104)]:
            self.bar(i)["m20"] = value
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["watch", "confirmation"])
        self.assertEqual(events[0]["pyramid"], events[1]["pyramid"])

    def test_review_confirmation_window_expires_after_twelve_bars(self):
        self.rows[:] = self.rows[:13]
        for _ in range(15):
            last = self.rows[-1]
            next_time = search.timestamp(last["t"]) + dt.timedelta(minutes=30)
            if next_time.time() >= dt.time(20):
                next_time = (next_time + dt.timedelta(days=1)).replace(hour=9, minute=0)
            m60 = last["m60"] + .05
            self.rows.append({**last, "t": next_time.isoformat(timespec="minutes"), "m60": m60,
                              "m3": m60 - 1, "m20": 100})
        self.rows[-1]["m3"] = self.rows[-1]["m60"] + 1
        self.assertEqual([event["stage"] for event in self.detect()], ["watch"])

    def test_confirmation_is_actual_cross_even_if_jump_exceeds_review_gap(self):
        self.bar(14)["m3"] = 110
        confirmation = self.detect()[1]
        self.assertEqual(confirmation["time"], self.bar(14)["t"])
        self.assertGreater(confirmation["gapPercent"], 2.5)

    def test_touch_is_not_confirmation_but_next_actual_cross_is(self):
        self.bar(14)["m3"] = self.bar(14)["m60"]
        self.assertEqual(self.detect()[1]["time"], self.bar(15)["t"])

    def test_below_band_exit_cancels_confirmation_without_repeat_for_same_peak(self):
        self.bar(12)["m3"] = self.bar(12)["m60"] * .97
        self.assertEqual([event["stage"] for event in self.detect()], ["watch"])

    def test_ma60_trend_loss_cancels_confirmation(self):
        self.bar(12)["m60"] = self.bar(11)["m60"] - .5
        self.assertEqual([event["stage"] for event in self.detect()], ["watch"])

    def test_invalid_data_after_review_cancels_confirmation(self):
        for key in ("m3", "m20", "m60", "c"):
            for invalid in (None, 0, True, float("nan")):
                self.setUp()
                self.bar(12)[key] = invalid
                self.assertEqual([event["stage"] for event in self.detect()], ["watch"])

    def test_missing_regular_bar_after_review_cancels_confirmation(self):
        del self.rows[13]
        self.assertEqual([event["stage"] for event in self.detect()], ["watch"])

    def test_aftermarket_empty_slots_and_next_morning_cross(self):
        self.rows[:] = self.rows[:16]
        for row in self.rows[1:]:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=3)).isoformat(timespec="minutes")
        self.bar(12)["t"] = "2026-10-15T19:00+09:00"
        self.bar(13)["t"] = "2026-10-15T19:30+09:00"
        self.bar(14)["t"] = "2026-10-16T09:00+09:00"
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["watch", "confirmation"])
        self.assertEqual(events[-1]["completedAt"], "2026-10-16T09:30+09:00")

    def test_ma10_ma40_and_daily_ma5_are_not_extra_filters(self):
        expected = self.detect()
        for row in self.rows:
            row.update(m10=None, m40=None, m120=None)
        self.candidate["displayCharts"]["daily"] = {"history": [{"m5": 1, "l": 999}]}
        self.assertEqual(self.detect(), expected)

    def test_pipeline_review_once_then_confirmation_without_turn_message(self):
        self.assertEqual(self.run_at("10:00"), 0)
        self.assertEqual(self.run_at("14:59"), 0)
        self.assertEqual(self.run_at("15:00"), 1)
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertEqual(self.run_at("16:00"), 0)
        self.assertEqual(self.run_at("16:30"), 1)
        self.assertEqual(self.run_at("16:30"), 0)
        self.assertEqual([call.args[4]["stage"] for call in self.sender.call_args_list], ["watch", "confirmation"])

    def test_pipeline_no_display_or_send_without_ma20_pyramid(self):
        self.run_at("10:00")
        for row in self.rows:
            row["m20"] = 100
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertEqual(self.payload["imageSearch"]["events"], [])
        self.assertEqual(self.payload["imageSearch"]["matches"], [])
        self.sender.assert_not_called()

    def test_incomplete_and_future_bars_are_not_used(self):
        self.run_at("10:00")
        self.bar(11)["complete"] = False
        self.assertEqual(self.run_at("15:00"), 0)
        self.bar(11)["complete"] = True
        self.assertEqual(self.run_at("15:00"), 1)

    def test_aftermarket_retry_is_not_consumed_and_history_is_not_replayed(self):
        for row in self.rows[1:]:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=3)).isoformat(timespec="minutes")
        self.run_at("13:00")
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("18:00"), 0)
        self.sender.side_effect = None
        self.assertEqual(self.run_at("18:00"), 1)
        self.assertEqual(self.run_at("18:00"), 0)
        self.setUp()
        self.assertEqual(self.run_at("19:00"), 0)
        self.sender.assert_not_called()

    def test_stale_unconfirmed_and_retired_candidates_do_not_notify(self):
        self.run_at("10:00")
        self.board["records"][0]["conditionConfirmed"] = False
        self.assertEqual(self.run_at("15:00"), 0)
        self.board["records"][0]["conditionConfirmed"] = True
        self.candidate["displayCharts"]["intraday"]["dataStatus"] = "stale"
        self.assertEqual(self.run_at("15:00"), 0)
        self.candidate["displayCharts"]["intraday"]["dataStatus"] = "ok"
        self.candidate["candidateTier"] = "expanded"
        self.assertEqual(self.run_at("15:00"), 0)

    def test_rule_does_not_modify_or_delete_study_board(self):
        before = copy.deepcopy(self.board)
        self.run_at("19:00")
        self.assertEqual(self.board, before)

    def test_messages_include_ma20_proof_daily_return_and_correct_phase(self):
        for event in self.detect():
            event["ruleName"] = self.rule["name"]
            row = next(row for row in self.rows if row["t"] == event["time"])
            response = Mock()
            response.read.return_value = b'{"attachment":{"url":"test"}}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch.object(colors, "chart_image", return_value=b"image"), \
                    patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
                search.send_alert("test-only", self.candidate, self.payload, row, event, search.timestamp(event["completedAt"]))
            message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
            for text in ("선행 MA20 피라미드", "2.5%", "전일 대비 +3.00%", "봉 완성 시각"):
                self.assertIn(text, message)
            self.assertIn("1차 검토필요" if event["stage"] == "watch" else "MA60 돌파 확인", message)


if __name__ == "__main__":
    unittest.main()
