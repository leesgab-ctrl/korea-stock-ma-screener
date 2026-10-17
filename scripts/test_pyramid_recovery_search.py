import datetime as dt
import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
import image_pattern_search as search


class PyramidRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.rule = next(rule for rule in json.loads(search.RULES_PATH.read_text(encoding="utf-8"))["rules"]
                         if rule["type"] == "ma20_pyramid_ma60_recovery")
        start = search.timestamp("2026-10-16T09:00+09:00")
        m20 = [100, 101, 102, 103, 104, 105, 106, 105, 104, 103, 102, 101, 100, 100]
        m3 = [102] * 9 + [99, 98, 98.5, 99.5, 101]
        self.rows = [{"t": (start + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                      "m3": value, "m10": 101, "m20": m20[i], "m40": 105 - i * .05,
                      "m60": 100 + i * .05, "c": 103, "complete": True} for i, value in enumerate(m3)]
        self.context = [{**self.rows[0], "t": "2026-10-15T19:30+09:00", "m60": 99.95}]
        self.candidate = {"code": "000003", "name": "Pyramid", "candidateTier": "expanded_ak",
                          "registeredAt": "2026-10-15T09:00+09:00", "displayCharts": {
                              "intraday": {"dataStatus": "ok", "series": self.context + self.rows}, "daily": {"history": []},
                              "quote": {"price": 103, "previousClose": 100, "time": "2026-10-16T16:00+09:00"}}}
        self.payload = {"candidates": [self.candidate]}
        self.board = {"version": 1, "records": [{"id": image, "conditionConfirmed": True,
                    "conditionConfirmedAt": "2026-10-16T08:00+09:00"} for image in self.rule["imageIds"]]}
        self.sender = Mock()

    def detect(self):
        return search.detect(self.context + self.rows, self.rule)

    def run_at(self, clock, no_notify=False):
        return search.process(self.payload, search.timestamp("2026-10-16T" + clock + "+09:00"), "test-only", no_notify,
                              board=self.board, rules=[self.rule], sender=self.sender)[0]

    def test_first_rise_below_ma60_then_actual_cross(self):
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["primary", "confirmation"])
        self.assertEqual([event["time"] for event in events], [self.rows[i]["t"] for i in (11, 13)])
        self.assertLess(events[0]["m3"], events[0]["m60"])
        self.assertGreater(events[1]["m3"], events[1]["m60"])
        self.assertEqual(events[0]["episodeTime"], events[1]["episodeTime"])

    def test_ma40_direction_and_clustering_are_not_filters(self):
        for slope in [-1, 0, 1]:
            for i, row in enumerate(self.rows):
                row["m40"] = 200 + slope * i
            self.assertEqual(len(self.detect()), 2)

    def test_ma60_must_be_rising_not_flat_or_falling(self):
        for slope in [0, -.01]:
            for i, row in enumerate(self.rows):
                row["m60"] = 100 + i * slope
            self.assertEqual(self.detect(), [])

    def test_no_pyramid_if_ma20_flat_or_only_falling(self):
        for values in [[100] * len(self.rows), [120 - i for i in range(len(self.rows))]]:
            for row, value in zip(self.rows, values):
                row["m20"] = value
            self.assertEqual(self.detect(), [])

    def test_shallow_or_slow_fall_is_not_pyramid(self):
        for row in self.rows[7:]:
            row["m20"] = 105.5
        self.assertEqual(self.detect(), [])
        self.setUp()
        rule = {**self.rule, "minFallSpeedRatio": 2}
        self.assertEqual(search.detect(self.context + self.rows, rule), [])

    def test_threshold_is_ma20_daily_change_not_stock_price(self):
        self.assertGreaterEqual(self.detect()[0]["riseDayPercent"], 3)
        self.context[0]["m20"] = 106 / 1.025
        for row in self.rows:
            row["c"] = 200
        self.assertEqual(self.detect(), [])

    def test_exactly_three_percent_qualifies_but_less_does_not(self):
        self.context[0]["m20"] = 106 / 1.03
        self.assertEqual(len(self.detect()), 2)
        self.context[0]["m20"] += .001
        self.assertEqual(self.detect(), [])

    def test_five_percent_only_marks_strength_without_changing_stages(self):
        for percent, strong in [(3, False), (4.999, False), (5, True), (5.01, True)]:
            self.context[0]["m20"] = 106 / (1 + percent / 100)
            events = self.detect()
            self.assertEqual([event["stage"] for event in events], ["primary", "confirmation"])
            self.assertEqual([event["strongRise"] for event in events], [strong, strong])

    def test_rising_leg_between_three_and_five_percent_is_included(self):
        for row in self.rows:
            row["m20"] = 100 + (row["m20"] - 100) * 2 / 3
        events = self.detect()
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0]["risePercent"], 4)
        self.assertFalse(events[0]["strongRise"])

    def test_daily_baseline_is_required(self):
        self.assertEqual(search.detect(self.rows, self.rule), [])

    def test_cross_without_observed_dip_does_not_match(self):
        self.context[0]["m3"] = 99
        for row in self.rows[:10]:
            row["m3"] = 99
        self.assertEqual(self.detect(), [])

    def test_touch_is_not_confirmation_but_later_cross_can_confirm(self):
        self.rows[-1]["m3"] = self.rows[-1]["m60"]
        self.assertEqual([event["stage"] for event in self.detect()], ["primary"])
        self.rows.append({**self.rows[-1], "t": "2026-10-16T16:00+09:00", "m3": 102, "m60": 101})
        self.assertEqual([event["stage"] for event in self.detect()], ["primary", "confirmation"])

    def test_turn_and_cross_in_one_bar_send_confirmation_only(self):
        self.rows[11]["m3"] = 102
        events = self.detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], "confirmation")
        self.assertTrue(events[0]["simultaneous"])

    def test_failed_rebound_is_not_rearmed_by_same_peak(self):
        self.rows[12]["m3"] = 98
        self.assertEqual([event["stage"] for event in self.detect()], ["primary"])

    def test_flat_ma3_can_wait_for_cross(self):
        self.rows[12]["m3"] = self.rows[11]["m3"]
        self.assertEqual(len(self.detect()), 2)

    def test_confirmation_timeout_and_lost_ma60_trend(self):
        self.assertEqual(len(search.detect(self.context + self.rows, {**self.rule, "confirmationMaxBars": 1})), 1)
        self.rows[-1]["m60"] = self.rows[-2]["m60"]
        self.assertEqual(len(self.detect()), 1)

    def test_invalid_values_and_missing_bar_do_not_bridge_setup(self):
        for key, value in [("m20", None), ("m60", float("nan")), ("c", True), ("m3", 0)]:
            saved = self.rows[10][key]
            self.rows[10][key] = value
            self.assertEqual(self.detect(), [])
            self.rows[10][key] = saved
        del self.rows[10]
        self.assertEqual(self.detect(), [])

    def test_no_future_bars_are_needed_for_primary(self):
        self.assertEqual(search.detect(self.context + self.rows[:12], self.rule), self.detect()[:1])
        for row in self.rows[12:]:
            row["m20"] = 200
        self.assertEqual(self.detect()[:1], search.detect(self.context + self.rows[:12], self.rule))

    def test_overnight_confirmation_uses_completed_next_session_bars(self):
        self.context[0]["t"] = "2026-10-14T19:30+09:00"
        for i, row in enumerate(self.rows[:12]):
            row["t"] = (search.timestamp("2026-10-15T14:00+09:00") + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes")
        for i, row in enumerate(self.rows[12:]):
            row["t"] = (search.timestamp("2026-10-16T09:00+09:00") + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes")
        self.assertEqual(len(self.detect()), 2)

    def test_two_reference_images_produce_one_primary_and_one_confirmation(self):
        self.assertEqual(self.run_at("14:30"), 0)
        self.rows[11]["complete"] = False
        self.assertEqual(self.run_at("15:00"), 0)
        self.rows[11]["complete"] = True
        self.assertEqual(self.run_at("15:00", no_notify=True), 0)
        self.assertEqual(self.run_at("15:00"), 1)
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertEqual(self.run_at("16:00"), 1)
        self.assertEqual(self.run_at("16:00"), 0)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.payload["imageSearch"]["matches"][0]["sourceImageIds"], self.rule["imageIds"])

    def test_each_confirmed_reference_can_enable_the_shared_rule(self):
        self.board["records"][0]["conditionConfirmed"] = False
        self.run_at("14:30")
        self.assertEqual(self.run_at("15:00"), 1)
        self.assertEqual(self.payload["imageSearch"]["matches"][0]["sourceImageIds"], self.rule["imageIds"][1:])

    def test_initial_history_does_not_replay_and_retired_candidates_are_excluded(self):
        self.assertEqual(self.run_at("16:00"), 0)
        self.assertTrue(self.payload["imageSearch"]["matches"])
        self.candidate["candidateTier"] = "expanded"
        self.assertEqual(self.run_at("16:00"), 0)
        self.assertFalse(self.payload["imageSearch"]["matches"])

    def test_unconfirmed_or_stale_does_not_send(self):
        self.run_at("14:30")
        for record in self.board["records"]:
            record["conditionConfirmed"] = False
        self.assertEqual(self.run_at("15:00"), 0)
        for record in self.board["records"]:
            record["conditionConfirmed"] = True
        self.candidate["displayCharts"]["intraday"]["dataStatus"] = "stale"
        self.assertEqual(self.run_at("15:00"), 0)

    def test_aftermarket_and_send_failure_retry(self):
        for row in self.rows:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=2)).isoformat(timespec="minutes")
        self.run_at("16:30")
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("17:00"), 0)
        self.sender.side_effect = None
        self.assertEqual(self.run_at("17:00"), 1)
        self.assertEqual(self.run_at("18:00"), 1)

    def test_pyramid_alert_does_not_merge_with_other_channels(self):
        self.run_at("14:30")
        other = {**self.rule, "id": "other", "name": "Other", "imageIds": ["other-image"]}
        self.board["records"].append({**self.board["records"][0], "id": "other-image"})
        event = self.detect()[0]
        with patch.object(search, "detect", side_effect=lambda rows, rule: [dict(event, ruleId=rule["id"],
                               signalType="ma20Pyramid" if rule["id"] == self.rule["id"] else "ma3Rise")]):
            search.process(self.payload, search.timestamp("2026-10-16T14:30+09:00"), "test-only", False,
                           board=self.board, rules=[self.rule, other], sender=self.sender)
            count, _ = search.process(self.payload, search.timestamp("2026-10-16T15:00+09:00"), "test-only", False,
                                      board=self.board, rules=[self.rule, other], sender=self.sender)
        self.assertEqual(count, 2)

    def test_messages_use_correct_stage_and_signed_daily_change(self):
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
            self.assertIn("피라미드", message)
            self.assertIn("전일 대비 +3.00%", message)
            self.assertIn("강한 상승 이력", message)
            self.assertIn("MA60 아래", message) if event["stage"] == "primary" else self.assertIn("MA60을 상향 돌파", message)
            self.assertNotIn("MA40을 상향 돌파", message)


if __name__ == "__main__":
    unittest.main()
