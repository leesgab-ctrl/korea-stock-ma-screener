import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import urllib.parse

import image_pattern_search as search
import update_candidate_monitor as monitor
import color_notifications as colors


def at(value):
    return dt.datetime.fromisoformat(f"2026-10-15T{value}:00+09:00")


def fixture():
    rows = []
    for i in range(4):
        rows.append({"t": (at("09:00") + dt.timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                     "c": 99 - i, "m3": 99 - i, "m10": 100 - i, "m20": 104 - i,
                     "m40": 110 - i, "m60": 116 - i, "complete": True})
    rows += [dict(t="2026-10-15T11:00+09:00", c=110, m3=108, m10=100, m20=101, m40=106, m60=112, complete=True),
             dict(t="2026-10-15T11:30+09:00", c=109, m3=107.5, m10=106.5, m20=102, m40=105, m60=111, complete=True)]
    candidate = {"code": "000001", "name": "Example", "registeredAt": "2026-10-14T16:40+09:00", "candidateTier": "core",
                 "displayCharts": {"intraday": {"dataStatus": "ok", "series": rows}, "daily": {"history": []}}}
    rules = [dict(id="first", version=1, name="Daesung", type="decline_ma40_recovery", enabled=True,
                  declineComparisons=3, setupLookback=20, imageIds=["image-first"], description="two-stage"),
             dict(id="second", version=1, name="Daehan", type="decline_ma10_ma40", enabled=True,
                  declineComparisons=3, setupLookback=20, imageIds=["image-second"], description="MA10-only")]
    board = {"version": 1, "records": [dict(id=key, conditionConfirmed=True, conditionConfirmedAt=at("08:00").isoformat(),
                                            updatedAt=at("08:00").isoformat()) for key in ["image-first", "image-second"]]}
    return {"candidates": [candidate]}, rules, board


class ImagePatternSearchTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.rules, self.board = fixture()
        self.sender = Mock()

    def run_at(self, clock, **kwargs):
        return search.process(self.payload, at(clock), "test-topic", False, board=self.board, rules=self.rules, sender=self.sender, **kwargs)

    def test_independent_rules_and_short_ma3_pullback_at_confirmation(self):
        rows = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]
        first = search.detect(rows, self.rules[0])
        second = search.detect(rows, self.rules[1])
        self.assertEqual([event["stage"] for event in first], ["primary", "confirmation"])
        self.assertEqual([event["stage"] for event in second], ["confirmation"])
        self.assertEqual(first[-1]["completedAt"], "2026-10-15T12:00+09:00")
        self.assertLess(rows[-1]["m3"], rows[-2]["m3"])

    def test_two_images_are_not_one_rule_and_overlap_sends_once(self):
        self.assertEqual(self.run_at("11:00")[0], 0)
        self.assertEqual(self.run_at("11:30")[0], 1)
        self.assertEqual(self.run_at("12:00")[0], 1)
        self.assertEqual(self.sender.call_count, 2)
        self.assertIn("Daesung / Daehan", self.sender.call_args.args[4]["ruleName"])
        self.assertEqual(self.run_at("12:10")[0], 0)
        self.board["records"][0]["conditionConfirmed"] = False
        self.assertEqual(self.run_at("12:20")[0], 0)
        self.assertEqual(len(self.payload["imageSearch"]["rules"]), 2)
        self.assertEqual([event["ruleId"] for event in self.payload["imageSearch"]["matches"]], ["second"])

    def test_second_image_alone_has_no_primary_alert(self):
        self.board["records"][0]["conditionConfirmed"] = False
        self.run_at("11:00")
        self.assertEqual(self.run_at("11:30")[0], 0)
        self.assertEqual(self.run_at("12:00")[0], 1)
        self.assertEqual(self.sender.call_args.args[4]["ruleName"], "Daehan")

    def test_no_historical_replay_on_initial_enable(self):
        self.assertEqual(self.run_at("12:05")[0], 0)
        self.assertEqual(len(self.payload["imageSearch"]["matches"]), 2)
        self.sender.assert_not_called()

    def test_no_notify_does_not_mark_pending_event_sent(self):
        self.run_at("11:00")
        self.assertEqual(search.process(self.payload, at("11:30"), "test", True, board=self.board, rules=self.rules, sender=self.sender)[0], 0)
        self.assertEqual(self.run_at("11:40")[0], 1)

    def test_incomplete_future_and_stale_bars_cannot_alert(self):
        self.run_at("11:00")
        self.assertEqual(self.run_at("11:29")[0], 0)
        chart = self.payload["candidates"][0]["displayCharts"]["intraday"]
        chart["series"][4]["complete"] = False
        self.assertEqual(self.run_at("11:30")[0], 0)
        chart["series"][4]["complete"] = True
        chart["dataStatus"] = "stale"
        self.assertEqual(self.run_at("11:40")[0], 0)
        chart["dataStatus"] = "ok"
        self.assertEqual(self.run_at("11:50")[0], 1)

    def test_failures_retry_without_duplicate_success(self):
        self.run_at("11:00")
        self.sender.side_effect = OSError("offline")
        self.assertEqual(self.run_at("11:30")[0], 0)
        self.assertTrue(self.payload["imageSearch"]["errors"])
        self.sender.side_effect = None
        self.assertEqual(self.run_at("11:40")[0], 1)
        self.assertEqual(self.run_at("11:50")[0], 0)

    def test_unconfirmed_deleted_unknown_and_retired_images_do_not_activate(self):
        self.board["records"][0]["conditionConfirmed"] = False
        self.board["records"][1]["deletedAt"] = "2026-10-15T08:00+09:00"
        self.run_at("11:00")
        self.assertEqual(self.run_at("12:00")[0], 0)
        self.assertEqual(self.payload["imageSearch"]["matches"], [])
        self.payload["candidates"][0]["candidateTier"] = "expanded"
        self.assertEqual(self.run_at("12:05")[0], 0)
        self.assertEqual(self.payload["imageSearch"]["candidateCount"], 0)

    def test_board_read_failure_fails_closed_preserving_dedup(self):
        self.run_at("11:00")
        previous = copy.deepcopy(self.payload["imageSearchNotificationState"])
        with patch.object(search, "load_board", side_effect=OSError("offline")):
            count, changed = search.process(self.payload, at("11:30"), "test", False, rules=self.rules, sender=self.sender)
        self.assertEqual((count, changed), (0, False))
        self.assertEqual(self.payload["imageSearchNotificationState"], previous)
        self.assertEqual(self.payload["imageSearch"]["status"], "error")

    def test_old_event_is_not_sent_but_aftermarket_events_are_sent(self):
        self.run_at("11:00")
        self.assertEqual(self.run_at("14:00")[0], 0)
        self.assertEqual(len(self.payload["imageSearch"]["matches"]), 2)
        self.payload, self.rules, self.board = fixture()
        rows = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]
        for row in rows:
            row["t"] = (search.timestamp(row["t"]) + dt.timedelta(hours=5)).isoformat(timespec="minutes")
        self.run_at("16:00")
        self.assertEqual(self.run_at("17:00")[0], 2)
        self.assertEqual(len(self.payload["imageSearch"]["matches"]), 2)

    def test_failed_primary_needs_a_new_declining_setup(self):
        rows = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]
        rows[-1].update(m3=99, m10=100)
        rows.append({**rows[-1], "t": "2026-10-15T12:00+09:00", "m3": 108, "m10": 101})
        self.assertEqual([event["stage"] for event in search.detect(rows, self.rules[0])], ["primary"])

    def test_actual_reference_examples(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "data/candidate-monitor.json").read_text(encoding="utf-8"))
        rules = json.loads((root / "data/image-search-rules.json").read_text(encoding="utf-8"))["rules"]
        examples = {"128820": (rules[0], "2026-10-14T09:30+09:00", "2026-10-14T11:00+09:00"),
                    "084010": (rules[1], None, "2026-10-13T16:30+09:00")}
        for code, (rule, primary, confirmation) in examples.items():
            item = next(item for item in payload["candidates"] + payload.get("history", []) if item["code"] == code)
            events = search.detect(search.completed_rows(item["displayCharts"]["intraday"], at("23:59")), rule)
            self.assertTrue(any(event["time"] == confirmation and event["stage"] == "confirmation" for event in events))
            if primary:
                self.assertTrue(any(event["time"] == primary and event["stage"] == "primary" for event in events))
            else:
                self.assertTrue(all(event["stage"] == "confirmation" for event in events))

    def test_message_transport_is_mocked_and_has_rule_and_stage(self):
        event = search.detect(self.payload["candidates"][0]["displayCharts"]["intraday"]["series"], self.rules[0])[0]
        event["ruleName"] = "Daesung"
        response = Mock()
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        row = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"][4]
        with patch.object(colors, "chart_image", return_value=b"mock-image"), patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
            search.send_alert("test-topic", self.payload["candidates"][0], self.payload, row, event, at("11:30"))
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        self.assertIn("Daesung", message)
        self.assertIn("1차 포착", message)
        self.assertIn("11:30", message)

    def test_intraday_pipeline_persists_notification_state(self):
        def process(payload, *args):
            payload["imageSearchNotificationState"] = {"test": {"baseline": "saved", "sent": {"event": "sent"}}}
            payload["imageSearch"] = {"matches": [{}]}
            return 1, True
        with tempfile.TemporaryDirectory() as folder:
            output, github = Path(folder) / "monitor.json", Path(folder) / "github-output"
            output.write_text(json.dumps(self.payload), encoding="utf-8")
            args = SimpleNamespace(mode="intraday", output=str(output), now=at("11:30").isoformat(), github_output=str(github),
                                   stock_data="missing-test-data.json", minute_count=60, no_notify=False)
            with patch.object(monitor, "parse_args", return_value=args), patch.object(monitor, "enrich", return_value=(0, 0)), \
                 patch.object(monitor, "refresh_display_charts"), patch.object(monitor, "refresh_excluded_verification", return_value=False), \
                 patch.object(monitor, "refresh_reference_verification", return_value=False), patch("paper_trading.update_paper"), \
                 patch.object(colors, "notify", return_value=(0, False)), patch("core_morning_notifications.notify", return_value=(0, False)), \
                 patch.object(monitor, "process_image_search", side_effect=process), patch.dict(monitor.os.environ, {"NTFY_TOPIC": "test"}):
                monitor.main()
            self.assertIn("imageSearchNotificationState", json.loads(output.read_text(encoding="utf-8")))
            self.assertIn("alerts=1", github.read_text())
            self.assertIn("color_state_changed=true", github.read_text())

    def test_images_only_pipeline_does_not_run_existing_alerts_or_paper_trading(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "monitor.json"
            output.write_text(json.dumps(self.payload), encoding="utf-8")
            args = SimpleNamespace(mode="images", output=str(output), now=at("17:00").isoformat(), github_output=None,
                                   stock_data="missing-test-data.json", minute_count=60, no_notify=False)
            with patch.object(monitor, "parse_args", return_value=args), patch.object(monitor, "enrich") as enrich, \
                 patch.object(monitor, "refresh_display_charts"), patch.object(monitor, "refresh_excluded_verification", return_value=False), \
                 patch.object(monitor, "refresh_reference_verification", return_value=False), patch("paper_trading.update_paper") as paper, \
                 patch.object(colors, "notify") as color, patch("core_morning_notifications.notify") as morning, \
                 patch.object(monitor, "process_image_search", return_value=(0, False)) as images:
                monitor.main()
            enrich.assert_not_called()
            paper.assert_not_called()
            color.assert_not_called()
            morning.assert_not_called()
            self.assertFalse(images.call_args.args[3])

    def test_daily_rebuild_retains_image_notification_state(self):
        with tempfile.TemporaryDirectory() as folder:
            stock = Path(folder) / "stocks.json"
            stock.write_text(json.dumps({"dates": ["2026-10-15"], "stocks": [{"c": "000000", "v": [1], "h": True}]}), encoding="utf-8")
            state = {"saved": {"baseline": "before", "sent": {"event": "sent"}}}
            result = monitor.build_daily_candidates(stock, {"candidates": [], "imageSearchNotificationState": state})
            self.assertEqual(result["imageSearchNotificationState"], state)


if __name__ == "__main__":
    unittest.main()
