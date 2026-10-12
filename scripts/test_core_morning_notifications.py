import datetime as dt
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import core_morning_notifications as morning
import color_notifications as colors
import update_candidate_monitor as monitor


class CoreMorningNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.payload = {"candidates": [{"code": "145720", "name": "덴티움", "displayCharts": {
            "intraday": {"series": [{"t": "2026-10-12T09:30:00", "c": 100}]},
            "daily": {"history": []}}}]}
        self.check = {"code": "145720", "previousDate": "2026-10-08", "ready": True, "matched": True,
                      "previousMa3": 100, "firstMa3": 101, "secondMa3": 102,
                      "row": {"t": "2026-10-12T09:30:00", "c": 100}}
        self.sender = Mock()
        self.evaluator = Mock(return_value=[self.check])

    def run_at(self, time="10:02", day="2026-10-12", **kwargs):
        return morning.notify(self.payload, dt.datetime.fromisoformat(f"{day}T{time}:00+09:00"), "test",
                              sender=self.sender, evaluator=self.evaluator, **kwargs)

    def test_sends_once_per_stock_and_day(self):
        self.assertEqual(self.run_at(), (1, True))
        self.assertEqual(self.run_at("10:12"), (0, False))
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.args[5], "coreMorningMa3")
        self.assertEqual(self.payload["coreMorningNotificationState"]["2026-10-12"]["145720"]["status"], "sent")
        self.assertEqual(self.run_at(day="2026-10-13"), (1, True))
        self.assertEqual(self.sender.call_count, 2)

    def test_fixed_opening_not_later_changes(self):
        self.check["matched"] = False
        self.assertEqual(self.run_at(), (0, True))
        self.check["matched"] = True
        self.assertEqual(self.run_at("10:30"), (0, False))
        self.sender.assert_not_called()

    def test_missing_data_can_retry_during_collection_window(self):
        self.check["ready"] = False
        self.assertEqual(self.run_at(), (0, False))
        self.check["ready"] = True
        self.assertEqual(self.run_at("10:10"), (1, True))

    def test_sender_failure_is_not_marked_sent(self):
        self.sender.side_effect = OSError("network unavailable")
        self.assertEqual(self.run_at(), (0, True))
        self.assertEqual(self.payload["coreMorningNotificationState"]["2026-10-12"]["145720"]["status"], "retry")
        self.sender.side_effect = None
        self.assertEqual(self.run_at("10:20"), (1, True))

    def test_no_intraday_recount_or_afterhours_replay(self):
        for time in ["09:30", "09:59", "11:00", "13:00", "20:00"]:
            self.assertEqual(self.run_at(time), (0, False))
        self.assertEqual(self.run_at(day="2026-10-10"), (0, False))
        self.assertEqual(self.run_at(no_notify=True), (0, False))
        self.evaluator.assert_not_called()
        self.sender.assert_not_called()

    def test_only_core_results_are_used(self):
        self.evaluator.return_value = []
        self.assertEqual(self.run_at(), (0, False))
        self.sender.assert_not_called()

    def test_korea_time_even_for_utc_input(self):
        utc = dt.datetime.fromisoformat("2026-10-12T01:05:00+00:00")
        self.assertEqual(morning.notify(self.payload, utc, "test", sender=self.sender, evaluator=self.evaluator), (1, True))

    def test_node_bridge_uses_native_ma3_and_the_ui_core_filter(self):
        def bar(t, m3, m40):
            return {"t": t, "m3": m3, "m10": 100.6, "m20": 100.7, "m40": m40, "m60": 100, "complete": True}
        self.payload["candidates"][0]["registeredAt"] = "2026-10-07T16:40:00+09:00"
        self.payload["candidates"][0]["displayCharts"]["intraday"]["series"] = [
            bar("2026-10-07T15:00:00", 100.5, 100),
            bar("2026-10-08T19:00:00", 100.5, 100.1),
            bar("2026-10-08T19:30:00", 100.5, 100.2),
            bar("2026-10-12T09:00:00", 101, 100.2),
            bar("2026-10-12T09:30:00", 102, 100.2),
            bar("2026-10-12T10:00:00", 50, 100.2),
        ]
        checks = morning.opening_checks(self.payload, "2026-10-12")
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["matched"])
        self.assertEqual(checks[0]["previousMa3"], 100.5)
        self.assertEqual(checks[0]["secondMa3"], 102)

    def test_message_and_transport_without_real_notification(self):
        response = Mock()
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(colors, "chart_image", return_value=b"image"), patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
            colors.send("test", self.payload["candidates"][0], self.payload, self.check["row"], self.check, "coreMorningMa3",
                        dt.datetime.fromisoformat("2026-10-12T10:02:00+09:00"))
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        self.assertIn("핵심종목 덴티움 가 전일대비 MA 3이 2회이상 상승하였습니다.", message)
        self.assertIn("매수를 고려한다면 MA3이 급히 상승시 하락안정을 고려하여 판단하기 바랍니다.", message)
        self.assertIn("09:30 / 10:00", message)
        self.assertNotIn("포착기준매수가", message)

    def test_intraday_pipeline_persists_the_new_deduplication_state(self):
        def notify(payload, *args):
            payload["coreMorningNotificationState"] = {"2026-10-12": {"145720": {"status": "sent"}}}
            return 1, True
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "monitor.json"
            github = Path(folder) / "github-output.txt"
            output.write_text(json.dumps(self.payload), encoding="utf-8")
            args = SimpleNamespace(mode="intraday", output=str(output), now="2026-10-12T10:02:00+09:00",
                                   github_output=str(github), stock_data="missing-test-data.json", minute_count=60, no_notify=False)
            with patch.object(monitor, "parse_args", return_value=args), patch.object(monitor, "enrich", return_value=(0, 0)), \
                    patch.object(monitor, "refresh_display_charts"), patch.object(monitor, "refresh_excluded_verification", return_value=False), \
                    patch.object(monitor, "refresh_reference_verification", return_value=False), patch("paper_trading.update_paper"), \
                    patch.object(colors, "notify", return_value=(0, False)), patch.object(morning, "notify", side_effect=notify) as called, \
                    patch.dict(monitor.os.environ, {"NTFY_TOPIC": "test"}):
                monitor.main()
            called.assert_called_once()
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["coreMorningNotificationState"]["2026-10-12"]["145720"]["status"], "sent")
            self.assertIn("alerts=1", github.read_text())
            self.assertIn("color_state_changed=true", github.read_text())


if __name__ == "__main__":
    unittest.main()
