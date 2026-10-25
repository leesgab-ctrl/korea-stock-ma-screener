import copy
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import color_notifications as colors
from common_monitor import merge_candidates
from first_rise_rules import VERSION
import first_rise_pullback as pullback
import image_pattern_search as search


def clock(value="2026-10-26T10:30+09:00"):
    return dt.datetime.fromisoformat(value)


def fixture():
    rule = next(rule for rule in json.loads(search.RULES_PATH.read_text(encoding="utf8"))["rules"]
                if rule["type"] == "first_rise_ma10_ma60_pullback")
    rows = [dict(t=t, m10=ma, m60=100, m3=99, m20=102, m40=101, c=price, complete=True)
            for t, ma, price in [("2026-10-23T14:00+09:00", 101, 104), ("2026-10-23T14:30+09:00", 104, 108),
                                 ("2026-10-26T09:00+09:00", 103, 104), ("2026-10-26T09:30+09:00", 102, 101),
                                 ("2026-10-26T10:00+09:00", 100.7, 100)]]
    candidate = dict(code="000001", name="QA", candidateTier="first_rise", dailySignalDate="2026-10-23",
                     registeredAt="2026-10-23T20:30+09:00", tradingDaysRemaining=9,
                     closeConfirmation={"ruleVersion": VERSION, "qualified": True},
                     firstRiseSignal={"time": rows[0]["t"]}, displayCharts={
                         "intraday": {"dataStatus": "ok", "series": rows},
                         "daily": {"history": [{"d": "2026-10-23", "o": 90, "c": 110}]},
                         "quote": {"time": rows[-1]["t"], "price": 100, "previousClose": 110}})
    board = {"version": 1, "records": [{"id": rule["imageIds"][0], "conditionConfirmed": True,
                                        "conditionConfirmedAt": "2026-10-25T15:30+09:00"}]}
    return {"candidates": [candidate]}, rule, board


class FirstRisePullbackTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.rule, self.board = fixture()
        self.item = self.payload["candidates"][0]
        self.rows = self.item["displayCharts"]["intraday"]["series"]

    def events(self):
        return search.detect(self.rows, self.rule, self.item)

    def run_at(self, current=None, sender=None, **kwargs):
        return search.process(self.payload, current or clock(), "test", False, board=self.board,
                              rules=[self.rule], sender=sender or Mock(), **kwargs)

    def test_approach_requires_prior_rise_then_fall_and_next_session(self):
        event = self.events()[0]
        self.assertAlmostEqual(event["gapPercent"], 0.7)
        self.assertFalse(event["closeApproach"])
        self.assertEqual(event["completedAt"], "2026-10-26T10:30+09:00")
        self.assertEqual(event["stage"], "watch")
        self.rows[0]["m10"] = 105
        self.assertEqual(self.events(), [])

    def test_inclusive_positive_negative_one_percent_and_half_percent(self):
        for gap in [1, 0.5, 0, -0.5, -1]:
            self.rows[-1]["m10"] = 100 + gap
            event = self.events()[0]
            self.assertAlmostEqual(event["gapPercent"], gap)
            self.assertEqual(event["closeApproach"], abs(gap) <= 0.5)
        for gap in [1.001, -1.001]:
            self.rows[-1]["m10"] = 100 + gap
            self.assertEqual(self.events(), [])

    def test_not_rising_or_flat_ma10_does_not_signal(self):
        self.rows[-1]["m10"] = self.rows[-2]["m10"] = 104
        self.assertEqual(self.events(), [])
        self.rows[-1]["m10"] = 104.5
        self.assertEqual(self.events(), [])

    def test_same_selection_day_never_signals(self):
        for i, row in enumerate(self.rows[2:]):
            row["t"] = f"2026-10-23T{15+i//2:02d}:{(i%2)*30:02d}+09:00"
        self.assertEqual(self.events(), [])

    def test_missing_values_and_missing_intraday_bar_reset_setup(self):
        self.rows[-2]["m10"] = float("nan")
        self.assertEqual(self.events(), [])
        self.rows[-2]["m10"] = 102
        self.rows[-1]["t"] = "2026-10-26T10:30+09:00"
        self.assertEqual(self.events(), [])

    def test_daily_fifty_percent_is_reference_not_a_filter_and_no_lookahead(self):
        self.rows[-1]["c"] = 80
        self.item["displayCharts"]["daily"]["history"].append({"d": "2026-10-26", "o": 1, "c": 9999})
        event = self.events()[0]
        self.assertEqual(event["dailyReference"]["retracementPercent"], 150)
        self.assertEqual(event["dailyReference"]["day"], "2026-10-23")
        self.item["displayCharts"]["daily"]["history"][0]["o"] = 120
        self.assertIsNone(self.events()[0]["dailyReference"])

    def test_only_current_confirmed_first_rise_members(self):
        for change in [{"candidateTier": "core"}, {"candidateTier": "ah"}, {"candidateTier": "first_rise_preliminary"},
                       {"tradingDaysRemaining": 0}, {"closeConfirmation": {"qualified": True, "ruleVersion": "old"}},
                       {"closeConfirmation": {"qualified": True, "ruleVersion": VERSION, "stage": "preliminary"}}]:
            candidate = {**self.item, **change}
            self.assertEqual(search.detect(self.rows, self.rule, candidate), [])

    def test_dual_membership_is_retained_without_changing_primary_tier_and_expires(self):
        primary = {"code": self.item["code"], "candidateTier": "core", "registeredAt": "2026-10-01T20:30+09:00",
                   "displayCharts": copy.deepcopy(self.item["displayCharts"])}
        merged = merge_candidates([primary], [self.item])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["candidateTier"], "core")
        self.assertEqual(len(search.detect(self.rows, self.rule, merged[0])), 1)
        self.assertIsNone(pullback.membership(merge_candidates(merged, [])[0]))

    def test_ah_dual_membership_never_uses_ah_enrollment_catchup(self):
        primary = {**copy.deepcopy(self.item), "candidateTier": "ah", "registeredAt": "2026-10-26T11:00+09:00"}
        self.payload["candidates"] = merge_candidates([primary], [self.item])
        sender = Mock()
        self.assertEqual(self.run_at(clock("2026-10-26T11:10+09:00"), sender)[0], 0)
        self.assertFalse(self.payload.get("ahImageEnrollmentState"))
        self.assertEqual(self.payload["imageSearch"]["events"][0]["membershipRegisteredAt"], self.item["registeredAt"])

    def test_pipeline_sends_once_acknowledged_and_marks_selection(self):
        sender = Mock()
        self.run_at(clock("2026-10-26T10:00+09:00"), sender)
        self.assertEqual(self.run_at(sender=sender)[0], 1)
        self.assertEqual(self.run_at(clock("2026-10-26T10:40+09:00"), sender)[0], 0)
        self.assertEqual(sender.call_count, 1)
        saved = next(iter(self.payload["imageSearchNotificationState"].values()))
        self.assertTrue(saved["selectionSentAt"])
        # Even a shortened data window with a new apparent rise/fall cannot re-notify this selection.
        for i, ma in enumerate([101, 103, 100.4]):
            self.rows[i]["t"] = f"2026-10-26T{11+i//2:02d}:{(i%2)*30:02d}+09:00"
            self.rows[i]["m10"] = ma
        del self.rows[3:]
        self.assertEqual(self.run_at(clock("2026-10-26T12:30+09:00"), sender)[0], 0)

    def test_send_failure_retries_without_receipt_or_selection_flag(self):
        sender = Mock(side_effect=RuntimeError("test"))
        self.run_at(clock("2026-10-26T10:00+09:00"), sender)
        self.assertEqual(self.run_at(sender=sender)[0], 0)
        saved = next(iter(self.payload["imageSearchNotificationState"].values()))
        self.assertNotIn("selectionSentAt", saved)
        sender.side_effect = None
        self.assertEqual(self.run_at(clock("2026-10-26T10:40+09:00"), sender)[0], 1)

    def test_initial_baseline_and_no_notify_never_replay_old_signals(self):
        sender = Mock()
        self.assertEqual(self.run_at(sender=sender)[0], 0)
        sender.assert_not_called()
        self.payload.pop("imageSearchNotificationState")
        self.assertEqual(search.process(self.payload, clock(), "test", True, self.board, [self.rule], sender)[0], 0)
        sender.assert_not_called()

    def test_incomplete_future_stale_bad_data_and_unconfirmed_image_do_not_send(self):
        for case in ["incomplete", "future", "stale", "bad-data", "unconfirmed"]:
            payload, rule, board = fixture()
            sender = Mock()
            search.process(payload, clock("2026-10-26T10:00+09:00"), "test", False, board, [rule], sender)
            current = clock()
            if case == "incomplete":
                payload["candidates"][0]["displayCharts"]["intraday"]["series"][-1]["complete"] = False
            elif case == "future":
                current -= dt.timedelta(seconds=1)
            elif case == "stale":
                current += dt.timedelta(minutes=91)
            elif case == "bad-data":
                payload["candidates"][0]["displayCharts"]["intraday"]["dataStatus"] = "error"
            else:
                board["records"][0]["conditionConfirmed"] = False
            self.assertEqual(search.process(payload, current, "test", False, board, [rule], sender)[0], 0, case)
            sender.assert_not_called()

    def test_message_has_day_change_reference_warning_and_ack_marker(self):
        event = {**self.events()[0], "ruleName": self.rule["name"]}
        response = Mock()
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(colors, "chart_image", return_value=b"image"), patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
            search.send_alert("test", self.item, self.payload, self.rows[-1], event, clock())
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        for text in ["매수검토 접근 안내", "+0.70%", "-9.09%", "50.0%", "자동 제외 아님", "MA3 재상승은 아직", "즉시 매수 신호가 아닙니다"]:
            self.assertIn(text, message)
        marker = self.payload["notificationHistory"][0]
        self.assertEqual(marker["kind"], "imageFirstRisePullback")
        self.assertIn("firstRiseMa10Ma60", marker["id"])

    def test_nanoentek_reference_shape_with_offline_native_data(self):
        root = Path(__file__).resolve().parents[1] / "outputs"
        minute = root / "current-long-ma-pullback/charts/039860.json"
        daily = root / "daily-long-ma-pullback/charts/039860.json"
        if not minute.exists() or not daily.exists():
            self.skipTest("Offline reference caches are not deployed")
        from ah_rules import hybrid_rows
        chart = json.loads(minute.read_text(encoding="utf8"))
        rows = hybrid_rows(chart["history"], [], clock("2026-10-23T20:30+09:00"))
        # This verifies the image's shape, not actual membership or delivery history.
        self.item.update(dailySignalDate="2026-10-21", firstRiseSignal={})
        self.item["displayCharts"]["daily"] = json.loads(daily.read_text(encoding="utf8"))
        event = search.detect(rows, self.rule, self.item)[0]
        self.assertEqual(event["completedAt"], "2026-10-22T18:30+09:00")
        self.assertAlmostEqual(event["gapPercent"], 0.9145297873290215)


if __name__ == "__main__":
    unittest.main()
