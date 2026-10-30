import copy
import datetime as dt
import io
import json
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import color_notifications as colors
import notification_snapshot as delivery
from notification_history import record_delivery, restore_delivery_receipts
import update_candidate_monitor as monitor
import update_ah_monitor as ah_monitor


AT = dt.datetime.fromisoformat("2026-10-30T14:00:00+09:00")


def sample():
    quote = {"dataStatus": "ok", "source": "naver_quote", "price": 105, "previousClose": 100,
             "time": "2026-10-30T13:59:59+09:00"}
    return delivery.capture("000001", fetcher=lambda *_: quote, clock=lambda: AT)


class NotificationSnapshotTests(unittest.TestCase):
    def test_quote_price_change_and_timestamps_are_separate_from_signal(self):
        snapshot = sample()
        self.assertEqual(snapshot["price"], 105)
        self.assertAlmostEqual(snapshot["changePct"], 5)
        self.assertEqual(snapshot["ageSeconds"], 1)
        self.assertFalse(snapshot["stale"])
        for text in ["105원", "+5.00%", "시세 시각", "조회 시각"]:
            self.assertIn(text, delivery.message(snapshot))

    def test_missing_nonfinite_bool_naive_future_or_error_quote_is_not_guessed(self):
        good = {"dataStatus": "ok", "price": 105, "previousClose": 100, "time": AT.isoformat()}
        for change in [{"price": None}, {"price": float("nan")}, {"price": True}, {"previousClose": 0},
                       {"time": "2026-10-30T14:00"}, {"time": "2026-10-30T14:06+09:00"}, {"dataStatus": "error"}]:
            snapshot = delivery.capture("000001", fetcher=lambda *_: {**good, **change}, clock=lambda: AT)
            self.assertEqual(snapshot["status"], "error")
            self.assertIsNone(snapshot["price"])
            self.assertIn("대체하지 않음", delivery.message(snapshot))

    def test_network_failure_and_last_trade_age_are_explicit(self):
        snapshot = delivery.capture("000001", fetcher=Mock(side_effect=TimeoutError("test")), clock=lambda: AT)
        self.assertEqual(snapshot["errorType"], "TimeoutError")
        stale = delivery.capture("000001", fetcher=lambda *_: {"dataStatus": "ok", "price": 105, "previousClose": 100,
                                 "time": "2026-10-30T13:00+09:00"}, clock=lambda: AT)
        self.assertTrue(stale["stale"])
        self.assertIn("오래된", delivery.message(stale))

    def test_chart_alert_fetches_quote_after_render_before_post_and_saves_only_ack(self):
        row = {"t": "2026-10-30T13:00+09:00", "c": 100, "m3": 99}
        candidate = {"code": "000001", "name": "QA", "displayCharts": {"quote": {"price": 999},
                     "intraday": {"series": [row]}, "daily": {"history": []}}}
        state = {"previousMa3": 97, "firstMa3": 98, "secondMa3": 99}
        order, payload, snapshot = [], {}, sample()
        def chart(*_):
            order.append("chart")
            return b"image"
        def capture(*_):
            order.append("quote")
            return snapshot
        def post(*_, **__):
            order.append("post")
            return io.BytesIO(b'{"attachment":{"name":"ok"}}')
        with patch.object(colors, "chart_image", side_effect=chart), patch.object(delivery, "capture", side_effect=capture), \
                patch.object(delivery, "now", return_value=AT), patch.object(colors.urllib.request, "urlopen", side_effect=post) as opened:
            colors.send("test", candidate, payload, row, state, "coreMorningMa3", AT-dt.timedelta(minutes=10))
        self.assertEqual(order, ["chart", "quote", "post"])
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        self.assertIn("100원", message)
        self.assertIn("105원", message)
        receipt = payload["notificationHistory"][0]
        self.assertEqual(receipt["signalPrice"], 100)
        self.assertEqual(receipt["priceSnapshot"]["price"], 105)
        self.assertEqual(receipt["sentAt"], AT.isoformat())
        snapshot["price"] = 999
        self.assertEqual(receipt["priceSnapshot"]["price"], 105)
        failed = {}
        with patch.object(colors, "chart_image", return_value=b"image"), patch.object(delivery, "capture", return_value=sample()), \
                patch.object(colors.urllib.request, "urlopen", return_value=io.BytesIO(b'{}')):
            with self.assertRaises(RuntimeError):
                colors.send("test", candidate, failed, row, state, "coreMorningMa3", AT)
        self.assertNotIn("notificationHistory", failed)

    def test_legacy_receipt_is_never_backfilled_with_todays_price(self):
        payload = {}
        args = (payload, "000001", "QA", "2026-10-30T13:00+09:00", AT.isoformat(), "test", "test")
        self.assertTrue(record_delivery(*args))
        self.assertFalse(record_delivery(*args, price_snapshot=sample(), signal_price=100))
        self.assertNotIn("priceSnapshot", payload["notificationHistory"][0])
        self.assertFalse(restore_delivery_receipts(payload))

    def test_plain_signal_message_returns_price_receipt_only_on_ack(self):
        candidate = {"code": "000001", "name": "QA", "intraday": {"signalTime": "2026-10-30T13:00+09:00",
                     "signalPrice": 100, "signalMa3": 99, "signalTarget": "MA40", "signalMa20": 98,
                     "signalMa40": 97, "signalMa60": 96}}
        response = Mock(status=200)
        response.read.return_value = b'{"id":"ack"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(delivery, "capture", return_value=sample()), patch.object(delivery, "now", return_value=AT), \
                patch.object(monitor.urllib.request, "urlopen", return_value=response) as opened:
            receipt = monitor.notify_ntfy("test", candidate)
            message = json.loads(opened.call_args.args[0].data)["message"]
            self.assertIn("105원", message)
            self.assertEqual(receipt["priceSnapshot"]["price"], 105)
            response.read.return_value = b'{}'
            with self.assertRaises(RuntimeError):
                monitor.notify_ntfy("test", candidate)

    def test_ah_message_and_receipt_keep_the_same_pre_send_quote(self):
        import test_ah_monitor as ah_tests
        current, market, charts = ah_tests.AhDriverTests().fixture()
        sender = Mock()
        snapshot = sample()
        initial = {"initializedAt": current.isoformat(), "candidates": []}
        with patch.object(ah_monitor, "daily_match", return_value=True), patch.object(ah_monitor, "potential_match", return_value=True), \
                patch.dict("os.environ", {"NTFY_TOPIC": "test"}), patch.object(delivery, "capture", return_value=snapshot), \
                patch.object(delivery, "now", return_value=current):
            result = ah_monitor.update(initial, market, current, refresh_fn=lambda *_: copy.deepcopy(charts), sender=sender)
        self.assertEqual(sender.call_count, 1)
        self.assertIn("105원", sender.call_args.args[1])
        self.assertEqual(result["notificationHistory"][0]["priceSnapshot"], snapshot)


if __name__ == "__main__":
    unittest.main()
