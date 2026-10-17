import copy
import datetime as dt
import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
import image_pattern_search as search


class Ma60NearReboundTests(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(search.RULES_PATH.read_text(encoding="utf-8"))["rules"]
        self.rule = next(rule for rule in self.rules if rule["type"] == "ma3_ma60_near_rebound")
        start = search.timestamp("2026-10-16T09:00+09:00")
        self.rows = [{"t": (start + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                      "m3": value, "m60": 100, "c": 103, "complete": True}
                     for i, value in enumerate([99, 98.5, 99, 100.5, 101])]
        self.candidate = {"code": "000003", "name": "Near MA60", "candidateTier": "expanded_ak",
                          "registeredAt": "2026-10-15T09:00+09:00", "displayCharts": {
                              "intraday": {"dataStatus": "ok", "series": self.rows}, "daily": {"history": []},
                              "quote": {"price": 103, "previousClose": 100, "time": "2026-10-16T16:00+09:00"}}}
        self.payload = {"candidates": [self.candidate]}
        self.board = {"version": 1, "records": [{"id": self.rule["imageIds"][0], "conditionConfirmed": True,
                    "conditionConfirmedAt": "2026-10-16T08:00+09:00"}]}
        self.sender = Mock()

    def detect(self):
        return search.detect(self.rows, self.rule)

    def run_at(self, clock, no_notify=False):
        return search.process(self.payload, search.timestamp("2026-10-16T" + clock + "+09:00"), "test-only", no_notify,
                              board=self.board, rules=[self.rule], sender=self.sender)[0]

    def test_only_sixth_image_and_old_pyramid_disabled(self):
        self.assertEqual(self.rule["imageIds"], ["7167e60c-b018-4cf6-9ccf-c3d40be2a815"])
        old = next(rule for rule in self.rules if rule["type"] == "ma20_pyramid_ma60_recovery")
        self.assertFalse(old["enabled"])
        self.assertTrue(self.rule["enabled"])
        self.assertEqual(self.rule["maxGapPercent"], 2)

    def test_turn_then_actual_cross_once(self):
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["primary", "confirmation"])
        self.assertEqual([event["time"] for event in events], [self.rows[i]["t"] for i in (2, 3)])
        self.assertEqual(events[0]["episodeTime"], events[1]["episodeTime"])
        self.assertFalse(any(event["simultaneous"] for event in events))

    def test_two_percent_boundary_on_trough_and_signal_both_sides(self):
        for values, expected in [([99, 98, 99], True), ([99, 97.999, 99], False),
                                 ([100, 99, 102], True), ([100, 99, 102.001], False),
                                 ([102, 101, 101.5], True), ([103, 102.001, 102.002], False)]:
            self.setUp()
            self.rows[:] = self.rows[:3]
            for row, value in zip(self.rows, values):
                row["m3"] = value
            self.assertEqual(bool(self.detect()), expected, values)

    def test_no_daily_ma5_ma20_pyramid_or_other_ma_dependency(self):
        before = self.detect()
        for row in self.rows:
            row.update(m20=float("nan"), m40=None, m10=False, m120=0)
        self.candidate["displayCharts"]["daily"] = {"history": [{"m5": 1, "l": 1000}]}
        self.assertEqual(self.detect(), before)
        self.run_at("15:00")
        self.assertTrue(self.payload["imageSearch"]["matches"])

    def test_continuous_rise_is_not_new_rebound(self):
        for i, row in enumerate(self.rows):
            row["m3"] = 98 + i * .5
        self.assertEqual(self.detect(), [])

    def test_flat_then_rise_is_rebound(self):
        self.rows[0]["m3"] = self.rows[1]["m3"]
        self.assertEqual(self.detect()[0]["stage"], "primary")

    def test_already_above_rebound_is_primary_not_false_confirmation(self):
        for row, value in zip(self.rows, [102, 100.5, 101, 101.5, 102]):
            row["m3"] = value
        self.assertEqual([event["stage"] for event in self.detect()], ["primary"])

    def test_simultaneous_rebound_cross_is_one_confirmation(self):
        self.rows[2]["m3"] = 100.5
        self.rows[3]["m3"] = 100.7
        events = self.detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], "confirmation")
        self.assertTrue(events[0]["simultaneous"])

    def test_icom_example_first_rebound_fits_and_next_extended_bar_does_not(self):
        # Native data has no 19:00 bar; use the actual observed bars rather than filling it.
        self.rows[:] = [dict(self.rows[0], t="2026-10-15T18:30+09:00", m3=6966.67, m60=6999.33),
                        dict(self.rows[0], t="2026-10-15T19:30+09:00", m3=6966.67, m60=7004.17),
                        dict(self.rows[0], t="2026-10-16T09:00+09:00", m3=7156.67, m60=7017.67, c=7520),
                        dict(self.rows[0], t="2026-10-16T09:30+09:00", m3=7390, m60=7034.83, c=7650)]
        events = self.detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["completedAt"], "2026-10-16T09:30+09:00")
        self.assertAlmostEqual(events[0]["gapPercent"], 1.9807, places=3)
        self.assertTrue(events[0]["simultaneous"])

    def test_hyungji_deep_below_rebound_excluded(self):
        self.rows[:] = self.rows[:3]
        for row, value in zip(self.rows, [371, 366.33, 377.33]):
            row.update(m3=value, m60=468.18)
        self.assertEqual(self.detect(), [])

    def test_fall_cancels_pending_and_next_turn_starts_new_episode(self):
        self.rows[3]["m3"] = 98.7
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["primary", "confirmation"])
        self.assertNotEqual(events[0]["episodeTime"], events[1]["episodeTime"])
        self.assertTrue(events[1]["simultaneous"])

    def test_out_of_band_cancels_pending_without_delayed_confirmation(self):
        self.rows[3]["m3"] = 103
        self.rows[4]["m3"] = 104
        self.assertEqual([event["stage"] for event in self.detect()], ["primary"])

    def test_invalid_values_and_missing_bar_break_context(self):
        for key in ("m3", "m60", "c"):
            for invalid in (None, 0, True, float("nan"), float("inf")):
                self.setUp()
                self.rows[1][key] = invalid
                self.assertEqual(self.detect(), [])
        self.setUp()
        del self.rows[1]
        self.assertEqual(self.detect(), [])

    def test_completed_only_notifications_once_and_no_historical_replay(self):
        self.assertEqual(self.run_at("10:00"), 0)
        self.assertEqual(self.run_at("10:29"), 0)
        self.rows[2]["complete"] = False
        self.assertEqual(self.run_at("10:30"), 0)
        self.rows[2]["complete"] = True
        self.assertEqual(self.run_at("10:30"), 1)
        self.assertEqual(self.run_at("10:30"), 0)
        self.assertEqual(self.run_at("11:00"), 1)
        self.assertEqual(self.run_at("11:00"), 0)
        self.setUp()
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertTrue(self.payload["imageSearch"]["matches"])
        self.sender.assert_not_called()

    def test_core_family_and_confirmed_image_switch(self):
        self.candidate["candidateTier"] = "core"
        self.run_at("10:00")
        self.board["records"][0]["conditionConfirmed"] = False
        self.assertEqual(self.run_at("10:30"), 0)
        self.assertFalse(self.payload["imageSearch"]["matches"])

    def test_aftermarket_send_failure_retries_without_consuming(self):
        for row in self.rows:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=7)).isoformat(timespec="minutes")
        self.run_at("17:00")
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("17:30"), 0)
        self.sender.side_effect = None
        self.assertEqual(self.run_at("17:30"), 1)
        self.assertEqual(self.run_at("18:00"), 1)

    def test_rule_disabled_keeps_board_and_removes_old_matches(self):
        old = next(rule for rule in self.rules if rule["type"] == "ma20_pyramid_ma60_recovery")
        board = copy.deepcopy(self.board)
        board["records"].append({**board["records"][0], "id": old["imageIds"][0]})
        before = copy.deepcopy(board)
        self.payload["imageSearch"] = {"events": [{"ruleId": old["id"]}], "matches": [{"ruleId": old["id"]}]}
        search.process(self.payload, search.timestamp("2026-10-16T15:00+09:00"), board=board, rules=[old, self.rule])
        self.assertEqual(board, before)
        self.assertTrue(all(event["ruleId"] == self.rule["id"] for event in self.payload["imageSearch"]["events"]))

    def test_unrelated_signal_does_not_consume_new_rule_delivery(self):
        self.run_at("10:00")
        other = {**self.rule, "id": "other", "name": "Other condition", "imageIds": ["other-image"]}
        self.board["records"].append({**self.board["records"][0], "id": "other-image"})
        event = self.detect()[0]
        with patch.object(search, "detect", side_effect=lambda rows, rule: [dict(event, ruleId=rule["id"],
                                signalType="ma3Ma60Near" if rule["id"] == self.rule["id"] else "ma3Rise")]):
            search.process(self.payload, search.timestamp("2026-10-16T10:00+09:00"), "test-only", False,
                           board=self.board, rules=[self.rule, other], sender=self.sender)
            count, _ = search.process(self.payload, search.timestamp("2026-10-16T10:30+09:00"), "test-only", False,
                                     board=self.board, rules=[self.rule, other], sender=self.sender)
        self.assertEqual(count, 2)

    def test_old_rule_state_does_not_replay_or_reset_other_rules(self):
        old_key = f"{self.candidate['code']}|{self.candidate['registeredAt']}|ma20-pyramid-ma60-recovery|1"
        self.payload["imageSearchNotificationState"] = {old_key: {"baseline": self.rows[0]["t"], "sent": {}}}
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertNotIn(old_key, self.payload["imageSearchNotificationState"])
        self.sender.assert_not_called()

    def test_notification_has_gap_and_signed_daily_change_not_pyramid(self):
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
            for expected in ("MA60", "±2%", "전일 대비 +3.00%", "봉 완성 시각"):
                self.assertIn(expected, message)
            self.assertNotIn("피라미드", message)
            self.assertNotIn("MA5", message)


if __name__ == "__main__":
    unittest.main()
