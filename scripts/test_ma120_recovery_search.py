import datetime as dt
import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
import image_pattern_search as search


class Ma120RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.rule = next(rule for rule in json.loads(search.RULES_PATH.read_text(encoding="utf-8"))["rules"]
                         if rule["type"] == "ma3_ma120_recovery")
        self.start = dt.datetime(2026, 10, 16, 9, tzinfo=search.KST)
        self.rows = [{"t": (self.start + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                      "m3": 99.9 + i * .01 if value == 100 else value,
                      "m40": 105, "m60": 110, "m120": 99.9 + i * .01, "c": 101, "complete": True}
                     for i, value in enumerate([102, 99, 101, 100, 100, 99, 101, 99, 101, 101])]
        self.candidate = {"code": "000002", "name": "MA120", "candidateTier": "expanded_ak",
                          "registeredAt": "2026-10-15T16:40+09:00", "displayCharts": {
                              "intraday": {"dataStatus": "ok", "series": self.rows}, "daily": {"history": []},
                              "quote": {"price": 103, "previousClose": 100, "time": "2026-10-16T15:00+09:00"}}}
        self.payload = {"candidates": [self.candidate]}
        self.board = {"version": 1, "records": [{"id": self.rule["imageIds"][0], "conditionConfirmed": True,
                    "conditionConfirmedAt": "2026-10-16T08:00+09:00"}]}
        self.sender = Mock()

    def detect(self):
        return search.detect(self.rows, self.rule)

    def run_at(self, clock, no_notify=False):
        return search.process(self.payload, search.timestamp("2026-10-16T" + clock + "+09:00"), "test-only", no_notify,
                              board=self.board, rules=[self.rule], sender=self.sender)[0]

    def test_crossing_bar_first_and_next_bar_equal_confirms(self):
        events = self.detect()
        self.assertEqual([event["time"] for event in events], [self.rows[i]["t"] for i in (2, 3, 6, 8, 9)])
        self.assertEqual([event["stage"] for event in events], ["primary", "confirmation", "primary", "primary", "confirmation"])
        self.assertEqual(events[0]["completedAt"], "2026-10-16T10:30+09:00")
        self.assertIn("MA120", events[1]["stageLabel"])
        self.assertEqual(events[1]["m3"], events[1]["m120"])

    def test_next_bar_can_fall_or_remain_flat_above_ma120(self):
        for value in (100.5, 101):
            self.rows[3]["m3"] = value
            self.assertEqual(self.detect()[1]["stage"], "confirmation")

    def test_just_touching_without_crossing_does_not_trigger_first(self):
        for i, row in enumerate(self.rows):
            row["m3"] = 102 if i == 0 else 99 if i == 1 else row["m120"]
        self.assertEqual(self.detect(), [])

    def test_failed_next_bar_is_not_confirmed_later(self):
        self.rows[3]["m3"] = 99
        self.rows[4]["m3"] = self.rows[4]["m120"]
        events = self.detect()
        self.assertNotIn(self.rows[3]["t"], [event["time"] for event in events])
        self.assertNotIn(self.rows[4]["t"], [event["time"] for event in events])

    def test_no_repeated_confirmation_during_a_continued_above_run(self):
        for row in self.rows[3:]:
            row["m3"] = 101
        self.assertEqual([event["stage"] for event in self.detect()], ["primary", "confirmation"])

    def test_long_dip_is_allowed_if_recent_below_bar_qualifies(self):
        for row in self.rows[1:-2]:
            row["m3"] = 99
            row.update(m40=120, m60=130)
        self.assertEqual(self.detect()[0]["time"], self.rows[-2]["t"])

    def test_ma120_must_be_rising_and_ma60_strictly_above_it(self):
        for mode in ("flat", "falling", "ma60Below", "ma60Equal"):
            self.setUp()
            for i, row in enumerate(self.rows):
                if mode in ("flat", "falling"):
                    row["m120"] = 100 - (i * .01 if mode == "falling" else 0)
                else:
                    row["m60"] = row["m120"] - (1 if mode == "ma60Below" else 0)
            self.assertEqual(self.detect(), [], mode)

    def test_ma60_may_fall_while_remaining_above_ma120(self):
        for i, row in enumerate(self.rows):
            row["m60"] = 110 - i * .1
        self.assertTrue(self.detect())

    def test_primary_requires_ma40_strictly_above_ma120_with_valid_data(self):
        for value in (99, self.rows[2]["m120"], None, float("nan"), float("inf"), True):
            self.setUp()
            self.rows[2]["m40"] = value
            events = self.detect()
            self.assertNotIn(self.rows[2]["t"], [event["time"] for event in events], str(value))
            self.assertIn(self.rows[3]["t"], [event["time"] for event in events], str(value))

    def test_ma40_gate_only_checks_crossing_bar_and_does_not_require_ma40_above_ma60(self):
        self.rows[1]["m40"] = 90
        event = self.detect()[0]
        self.assertEqual(event["stage"], "primary")
        self.assertTrue(event["requireMa40AboveMa120ForPrimary"])
        self.assertLess(event["m40"], event["m60"])

    def test_next_bar_confirmation_does_not_require_ma40_above_ma120(self):
        self.rows[3]["m40"] = 90
        events = self.detect()
        self.assertEqual(events[1]["stage"], "confirmation")
        self.assertFalse(events[1]["requireMa40AboveMa120ForPrimary"])

    def test_failed_primary_is_not_sent_later_when_ma40_recovers(self):
        self.rows[:] = self.rows[:5]
        self.rows[2]["m40"] = 90
        self.rows[3]["m3"] = 99
        self.run_at("10:00")
        self.assertEqual(self.run_at("10:30"), 0)
        self.assertEqual(self.run_at("11:00"), 0)
        self.assertEqual(self.run_at("11:30"), 0)
        self.sender.assert_not_called()

    def test_ah_enrollment_does_not_send_primary_that_fails_ma40_gate(self):
        self.rows[:] = self.rows[:3]
        self.rows[2]["m40"] = 90
        self.candidate.update(candidateTier="ah", registeredAt="2026-10-16T11:00+09:00", initialImport=False)
        self.assertEqual(self.run_at("11:10"), 0)
        self.sender.assert_not_called()

    def test_last_five_bars_not_start_of_dip_controls_eligibility(self):
        for distance, expected in [(1, True), (5, True), (6, False)]:
            self.setUp()
            self.rows[:] = self.rows[:distance + 2]
            for i, row in enumerate(self.rows):
                row["m3"] = 102 if i in (0, len(self.rows) - 1) else 99 if i == 1 else row["m120"]
            events = self.detect()
            self.assertEqual(bool(events), expected, distance)
            if expected:
                self.assertEqual(events[0]["setupTime"], self.rows[1]["t"])

    def test_recent_below_bar_must_also_have_the_required_background(self):
        self.rows[:] = self.rows[:3]
        self.rows[1]["m60"] = 90
        self.assertEqual(self.detect(), [])

    def test_no_new_primary_if_ma3_was_already_above(self):
        for row in self.rows:
            row["m3"] = 102
        self.assertEqual(self.detect(), [])

    def test_gs_global_october_16_cross_with_ma60_below_ma120_is_excluded(self):
        values = [
            ("09:30", 2866.67, 2947.33, 3036.67, 2930),
            ("10:00", 2895, 2945.67, 3034.92, 2895),
            ("10:30", 2951.67, 2946.17, 3034.33, 3030),
            ("11:00", 2990, 2946.5, 3034.12, 3045),
            ("11:30", 3066.67, 2948.67, 3034.33, 3125),
            ("12:00", 3096.67, 2950.17, 3034.46, 3120),
        ]
        rows = [{"t": f"2026-10-16T{clock}+09:00", "m3": m3, "m60": m60, "m120": m120,
                 "c": price, "complete": True} for clock, m3, m60, m120, price in values]
        self.assertEqual(search.detect(rows, self.rule), [])

    def test_new_rule_version_initializes_without_replaying_old_history(self):
        old_key = f"{self.candidate['code']}|{self.candidate['registeredAt']}|{self.rule['id']}|1"
        self.payload["imageSearchNotificationState"] = {old_key: {"baseline": self.rows[0]["t"], "sent": {}}}
        self.assertEqual(self.run_at("15:00"), 0)
        self.sender.assert_not_called()
        new_key = old_key[:-1] + str(self.rule["version"])
        self.assertEqual(self.payload["imageSearchNotificationState"][new_key]["baseline"], self.rows[-1]["t"])

    def test_confirmation_requires_background_and_is_not_retried_later(self):
        for field, value in [("m60", 90), ("m120", 99.92)]:
            self.setUp()
            self.rows[3][field] = value
            self.assertNotIn(self.rows[3]["t"], [event["time"] for event in self.detect()])
            self.assertNotIn(self.rows[4]["t"], [event["time"] for event in self.detect()])

    def test_invalid_ma120_and_same_day_missing_bar_cancel_confirmation(self):
        self.rows[3]["m120"] = None
        self.assertNotIn(self.rows[3]["t"], [event["time"] for event in self.detect()])
        self.rows[3]["m120"] = float("nan")
        self.assertNotIn(self.rows[3]["t"], [event["time"] for event in self.detect()])
        del self.rows[3]
        self.assertNotIn(self.rows[3]["t"], [event["time"] for event in self.detect()])

    def test_next_trading_bar_can_be_next_morning(self):
        self.rows[:] = self.rows[:4]
        for i, row in enumerate(self.rows[:3]):
            row["t"] = f"2026-10-15T{18+i//2:02d}:{'00' if i % 2 == 0 else '30'}+09:00"
        self.rows[3]["t"] = "2026-10-16T09:00+09:00"
        self.assertEqual([event["stage"] for event in self.detect()], ["primary", "confirmation"])

    def test_completed_only_two_stages_once(self):
        self.assertEqual(self.run_at("10:00"), 0)
        self.assertEqual(self.run_at("10:29"), 0)
        self.assertEqual(self.run_at("10:30", no_notify=True), 0)
        self.assertEqual(self.run_at("10:30"), 1)
        self.assertEqual(self.run_at("10:30"), 0)
        self.rows[3]["complete"] = False
        self.assertEqual(self.run_at("11:00"), 0)
        self.rows[3]["complete"] = True
        self.assertEqual(self.run_at("11:00"), 1)
        self.assertEqual(self.run_at("11:00"), 0)
        self.assertEqual(self.sender.call_count, 2)

    def test_core_family_is_also_scanned(self):
        self.candidate["candidateTier"] = "core"
        self.assertEqual(self.run_at("10:00"), 0)
        self.assertEqual(self.run_at("10:30"), 1)

    def test_initial_history_is_displayed_but_not_replayed(self):
        self.assertEqual(self.run_at("15:00"), 0)
        self.assertTrue(self.payload["imageSearch"]["matches"])
        self.assertEqual(self.run_at("15:00"), 0)

    def test_unconfirmed_stale_and_retired_candidates_do_not_notify(self):
        self.run_at("10:00")
        self.board["records"][0]["conditionConfirmed"] = False
        self.assertEqual(self.run_at("10:30"), 0)
        self.assertFalse(self.payload["imageSearch"]["rules"][0]["active"])
        self.board["records"][0]["conditionConfirmed"] = True
        self.candidate["displayCharts"]["intraday"]["dataStatus"] = "stale"
        self.assertEqual(self.run_at("10:30"), 0)
        self.candidate["displayCharts"]["intraday"]["dataStatus"] = "ok"
        self.candidate["candidateTier"] = "expanded"
        self.assertEqual(self.run_at("10:30"), 0)

    def test_aftermarket_and_failed_send_retry(self):
        for row in self.rows:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=7)).isoformat(timespec="minutes")
        self.run_at("17:00")
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("17:30"), 0)
        self.sender.side_effect = None
        self.assertEqual(self.run_at("17:30"), 1)
        self.assertEqual(self.run_at("18:00"), 1)

    def test_ma120_alert_does_not_merge_with_ma40_or_cluster_alert(self):
        self.run_at("10:00")
        other = {**self.rule, "id": "other", "name": "MA40", "imageIds": ["other-image"]}
        self.board["records"].append({**self.board["records"][0], "id": "other-image"})
        events = self.detect()[:1]
        with patch.object(search, "detect", side_effect=lambda rows, rule: [dict(events[0], ruleId=rule["id"],
                                signalType="ma3Ma120" if rule["id"] == self.rule["id"] else "ma3Rise")]):
            search.process(self.payload, search.timestamp("2026-10-16T10:00+09:00"), "test-only", False,
                           board=self.board, rules=[self.rule, other], sender=self.sender)
            count, _ = search.process(self.payload, search.timestamp("2026-10-16T10:30+09:00"), "test-only", False,
                                     board=self.board, rules=[self.rule, other], sender=self.sender)
        self.assertEqual(count, 2)

    def test_message_is_ma120_not_ma40_and_contains_day_change(self):
        for event in self.detect()[:2]:
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
            self.assertIn("MA120", message)
            self.assertIn("MA120 상승 중", message)
            self.assertIn("MA60 > MA120", message)
            if event["stage"] == "primary":
                self.assertIn("MA40 > MA120", message)
                self.assertIn("MA40 105.00", message)
            else:
                self.assertNotIn("MA40 > MA120", message)
            self.assertIn("전일 대비 +1.00%", message)
            self.assertNotIn("MA10이 MA40", message)
            if event["stage"] == "confirmation":
                self.assertIn("다음 완성봉", message)
                self.assertIn("추가 상승 여부와는 별개", message)


if __name__ == "__main__":
    unittest.main()
