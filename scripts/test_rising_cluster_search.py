import copy
import datetime as dt
import json
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import image_pattern_search as search
import color_notifications as colors


def fixture():
    rules = json.loads(search.RULES_PATH.read_text(encoding="utf-8"))["rules"]
    rule = next(item for item in rules if item["type"] == "rising_cluster_ma3")
    start = dt.datetime(2026, 10, 15, 9, tzinfo=search.KST)
    rows = [{"t": (start + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
             "c": value, "m3": value, "m10": 100.35, "m20": 100.3,
             "m40": 100 + i * .01, "m60": 99.8 + i * .01, "complete": True}
            for i, value in enumerate([100.5, 99.5, 100.6, 100.4, 100.2, 100.3, 101.8])]
    candidate = {"code": "000002", "name": "Cluster", "candidateTier": "expanded_ak",
                 "registeredAt": "2026-10-14T16:40+09:00",
                 "displayCharts": {"intraday": {"dataStatus": "ok", "series": rows}, "daily": {"history": []}}}
    board = {"version": 1, "records": [{"id": rule["imageIds"][0], "conditionConfirmed": True,
                                         "updatedAt": start.replace(hour=8).isoformat()}]}
    return {"candidates": [candidate]}, rule, board


class RisingClusterTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.rule, self.board = fixture()
        self.rows = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]
        self.sender = Mock()

    def detect(self):
        return search.detect(self.rows, self.rule)

    def run_at(self, value, no_notify=False):
        return search.process(self.payload, dt.datetime.fromisoformat(f"2026-10-15T{value}+09:00"),
                              "test", no_notify, board=self.board, rules=[self.rule], sender=self.sender)[0]

    def test_two_stages_and_confirmation_can_expand_beyond_one_percent(self):
        events = self.detect()
        self.assertEqual([event["stage"] for event in events], ["primary", "confirmation"])
        self.assertEqual([event["riseCount"] for event in events], [1, 2])
        self.assertEqual(events[0]["time"], self.rows[5]["t"])
        self.assertEqual(events[1]["completedAt"], "2026-10-15T12:30+09:00")
        self.assertLess(events[1]["clusterSpreadPercent"], 1)
        self.assertGreater(events[1]["currentSpreadPercent"], 1)
        self.assertEqual(events[0]["recoveryBars"], 1)

    def test_preceding_cluster_is_allowed_before_first_expansion(self):
        self.rows[5]["m3"] = 101.2
        events = self.detect()
        self.assertEqual(events[0]["setupTime"], self.rows[4]["t"])
        self.assertGreater(events[0]["currentSpreadPercent"], 1)
        self.assertLess(events[0]["clusterSpreadPercent"], 1)

    def test_cluster_limit_is_strictly_less_not_equal(self):
        values = [self.rows[5][key] for key in ("m3", "m10", "m20", "m40")]
        self.rule["clusterMaxPercent"] = (max(values) - min(values)) / min(values) * 100
        self.assertEqual(self.detect(), [])

    def test_ma60_is_not_part_of_the_cluster(self):
        for i, row in enumerate(self.rows):
            row["m60"] = 98 + i * .01
        self.rows[1]["m3"] = 97.5
        self.assertEqual(len(self.detect()), 2)
        self.assertEqual(self.detect()[0]["clusterLines"], [3, 10, 20, 40])

    def test_five_line_cluster_allows_flat_ma60_without_a_new_cross(self):
        for row in self.rows:
            row["m60"] = 99.8
        self.rows[1]["m3"] = 100.4
        self.rows[3]["m3"] = self.rows[4]["m3"] = 100.3
        self.rows[5]["m3"], self.rows[6]["m3"] = 100.4, 100.5
        events = self.detect()
        self.assertEqual([event["riseCount"] for event in events], [1, 2])
        self.assertTrue(all(event["clusterLines"] == [3, 10, 20, 40, 60] for event in events))
        self.assertTrue(all("5선 밀집" in event["stageLabel"] for event in events))
        self.assertFalse(events[0]["fastRecovery"])

    def test_five_line_cluster_does_not_allow_declining_ma60(self):
        for i, row in enumerate(self.rows):
            row["m60"] = 99.9 - i * .01
        self.assertEqual(self.detect(), [])

    def test_flat_ma60_requires_all_five_lines_not_only_four_to_cluster(self):
        for row in self.rows:
            row["m60"] = 98
        self.assertEqual(self.detect(), [])

    def test_five_line_strict_boundary_and_mixed_flat_rising_ma60(self):
        for row in self.rows:
            row["m60"] = 99.8
        values = [self.rows[5][key] for key in search.KEYS]
        self.rule["clusterMaxPercent"] = (max(values) - min(values)) / min(values) * 100
        self.assertEqual(self.detect(), [])
        self.rule["clusterMaxPercent"] = 1
        for i, row in enumerate(self.rows):
            row["m60"] = 99.8 + (i // 2) * .01
        self.assertEqual(len(self.detect()), 2)

    def test_flat_uses_full_range_not_just_net_change(self):
        self.rows[2]["m20"] = 102
        self.assertEqual(self.detect(), [])

    def test_half_percent_flat_limit_allows_point_four_but_not_point_six(self):
        self.rows[2]["m20"] = 100.7
        self.assertEqual(len(self.detect()), 2)
        self.rows[2]["m20"] = 100.9
        self.assertEqual(self.detect(), [])

    def test_flat_or_declining_long_ma_cannot_start_a_signal(self):
        self.rows[3]["m40"] = self.rows[2]["m40"]
        self.assertEqual(self.detect(), [])
        self.setUp()
        for row in self.rows:
            row["m40"] = row["m60"] - .1
        self.assertEqual(self.detect(), [])

    def test_no_dip_and_slow_recovery_do_not_block_signals(self):
        self.rows[1]["m3"] = 100.4
        events = self.detect()
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["fastRecovery"] is False and event["recoveryBars"] is None for event in events))
        self.setUp()
        for row in self.rows[2:6]:
            row["m3"] = 99.5
        events = self.detect()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["fastRecovery"])

    def test_expired_recovery_is_only_reference_and_does_not_block_signal(self):
        first = self.rows[:5]
        for i in range(5, 19):
            first.append({**first[4], "t": (search.timestamp(first[0]["t"]) + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                          "m40": 100 + .01 * i, "m60": 99.8 + .01 * i, "m3": 100.2})
        first[-1]["m3"] = 100.3
        self.rows[:] = first
        events = self.detect()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["fastRecovery"])

    def test_flat_next_bar_cancels_second_and_fresh_cluster_can_rearm(self):
        self.rows[6]["m3"] = self.rows[5]["m3"]
        for i, value in enumerate([100.4, 100.5, 100.6], start=7):
            self.rows.append({**self.rows[6], "t": (search.timestamp(self.rows[0]["t"]) + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                              "m40": 100 + i * .01, "m60": 99.8 + i * .01, "m3": value})
        self.assertEqual([event["stage"] for event in self.detect()], ["primary", "primary", "confirmation"])
        for row in self.rows[6:]:
            row["m10"] = 104
        self.assertEqual([event["stage"] for event in self.detect()], ["primary"])

    def test_missing_bar_invalid_data_or_ma3_decline_cancels_second(self):
        for change in [{"t": "2026-10-15T12:30+09:00"}, {"m10": None}, {"m3": 99}]:
            with self.subTest(change=change):
                rows = copy.deepcopy(self.rows)
                rows[6].update(change)
                self.assertEqual([event["stage"] for event in search.detect(rows, self.rule)], ["primary"])

    def test_new_dip_and_fast_recovery_can_start_a_new_episode(self):
        for i, value in enumerate([97, 102, 100.3, 100.4, 100.5], start=7):
            self.rows.append({**self.rows[4], "t": (search.timestamp(self.rows[0]["t"]) + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                              "m40": 100 + i * .01, "m60": 99.8 + i * .01, "m3": value})
        self.assertEqual([event["stage"] for event in self.detect()], ["primary", "confirmation", "primary", "confirmation"])

    def test_completed_only_two_notifications_once_and_no_history_replay(self):
        self.assertEqual(self.run_at("11:30"), 0)
        self.assertEqual(self.run_at("11:59"), 0)
        self.assertEqual(self.run_at("12:00"), 1)
        self.assertEqual(self.run_at("12:10"), 0)
        self.assertEqual(self.run_at("12:30"), 1)
        self.assertEqual(self.run_at("12:40"), 0)
        self.assertEqual(self.sender.call_count, 2)
        self.payload.pop("imageSearchNotificationState")
        self.assertEqual(self.run_at("12:45"), 0)

    def test_incomplete_second_waits_and_no_notify_does_not_consume_it(self):
        self.run_at("11:30")
        self.run_at("12:00")
        self.rows[6]["complete"] = False
        self.assertEqual(self.run_at("12:30"), 0)
        self.rows[6]["complete"] = True
        self.assertEqual(self.run_at("12:40", no_notify=True), 0)
        self.assertEqual(self.run_at("12:50"), 1)

    def test_unconfirmed_stale_and_failed_sender(self):
        self.run_at("11:30")
        self.payload["candidates"][0]["displayCharts"]["intraday"]["dataStatus"] = "stale"
        self.assertEqual(self.run_at("12:00"), 0)
        self.payload["candidates"][0]["displayCharts"]["intraday"]["dataStatus"] = "ok"
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("12:10"), 0)
        self.sender.side_effect = None
        self.assertEqual(self.run_at("12:20"), 1)
        self.board["records"][0]["conditionConfirmed"] = False
        self.assertEqual(self.run_at("12:30"), 0)
        self.assertEqual(self.payload["imageSearch"]["matches"], [])

    def test_aftermarket_remains_enabled(self):
        for row in self.rows:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=7)).isoformat(timespec="minutes")
        self.assertEqual(self.run_at("18:30"), 0)
        self.assertEqual(self.run_at("19:00"), 1)
        self.assertEqual(self.run_at("19:30"), 1)

    def test_new_transport_does_not_describe_a_ma10_breakout(self):
        event = self.detect()[-1]
        event["ruleName"] = self.rule["name"]
        response = Mock()
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(colors, "chart_image", return_value=b"test"), patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
            search.send_alert("test", self.payload["candidates"][0], self.payload, self.rows[6], event, search.timestamp(event["completedAt"]))
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        self.assertIn("2차 포착", message)
        self.assertIn("MA3가 2봉 연속 상승", message)
        self.assertIn("밀집", message)
        self.assertIn("필수 조건 아님", message)
        self.assertNotIn("MA10이 MA40", message)


if __name__ == "__main__":
    unittest.main()
