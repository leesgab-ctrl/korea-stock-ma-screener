import datetime as dt
import io
import json
import unittest
from unittest.mock import patch

import color_notifications as colors
from notification_history import record_delivery, restore_delivery_receipts

KST = dt.timezone(dt.timedelta(hours=9))


class DeliveryHistoryTests(unittest.TestCase):
    def test_only_confirmed_ntfy_delivery_is_recorded(self):
        row = {"t": "2026-10-23T09:30+09:00", "c": 100, "m3": 99}
        candidate = {"code": "000001", "name": "test", "registeredAt": "2026-10-22T17:00+09:00",
                     "displayCharts": {"intraday": {"series": [row]}, "daily": {"history": []}}}
        state = {"previousMa3": 97, "firstMa3": 98, "secondMa3": 99}
        current = dt.datetime(2026, 10, 23, 10, 2, tzinfo=KST)
        payload = {}
        with patch.object(colors, "chart_image", return_value=b"test image"), patch.object(
                colors.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps({"attachment": {"name": "chart"}}).encode())):
            colors.send("test-topic", candidate, payload, row, state, "coreMorningMa3", current)
        self.assertEqual(len(payload["notificationHistory"]), 1)
        self.assertEqual(payload["notificationHistory"][0]["barTime"], row["t"])
        self.assertEqual(payload["notificationHistory"][0]["sentAt"], current.isoformat())
        failed = {}
        with patch.object(colors, "chart_image", return_value=b"test image"), patch.object(
                colors.urllib.request, "urlopen", return_value=io.BytesIO(b"{}")):
            with self.assertRaises(RuntimeError):
                colors.send("test-topic", candidate, failed, row, state, "coreMorningMa3", current)
        self.assertNotIn("notificationHistory", failed)

    def test_receipt_restore_never_infers_from_detected_events(self):
        event = {"code": "000001", "time": "2026-10-22T19:00+09:00", "stage": "confirmation", "ruleName": "test condition"}
        payload = {"imageSearch": {"events": [event]}, "candidates": [{"code": "000001", "name": "test"}]}
        self.assertFalse(restore_delivery_receipts(payload))
        signature = "000001|2026-10-20T17:00+09:00|2026-10-22T19:00+09:00|confirmation"
        payload["imageSearchNotificationState"] = {"rule-a": {"sent": {signature: "2026-10-22T19:36:00+09:00"}},
                                                   "rule-b": {"sent": {signature: "2026-10-22T19:36:00+09:00"}}}
        self.assertTrue(restore_delivery_receipts(payload))
        self.assertEqual(len(payload["notificationHistory"]), 1)
        self.assertEqual(payload["notificationHistory"][0]["label"], "test condition")
        self.assertFalse(restore_delivery_receipts(payload))
        self.assertEqual(payload["imageSearch"]["events"], [event])

    def test_malformed_or_unconfirmed_records_are_not_restored(self):
        payload = {"imageSearchNotificationState": {"test": {"sent": {"invalid": "now", "000001|reg|wrong|primary": "now"}}},
                   "coreMorningNotificationState": {"2026-10-23": {"000001": {"status": "retry", "sentAt": "2026-10-23T10:00+09:00"}}}}
        self.assertFalse(restore_delivery_receipts(payload))
        self.assertFalse(payload.get("notificationHistory"))

    def test_legacy_opening_receipt_and_live_receipt_share_identity(self):
        payload = {"coreMorningNotificationState": {"2026-10-23": {"000001": {"status": "sent", "sentAt": "2026-10-23T10:03+09:00"}}}}
        self.assertTrue(restore_delivery_receipts(payload))
        self.assertFalse(record_delivery(payload, "000001", "test", "2026-10-23T09:30+09:00", "2026-10-23T10:03+09:00",
                                         "coreMorningMa3", "test"))
        self.assertEqual(len(payload["notificationHistory"]), 1)

    def test_invalid_or_future_bar_is_rejected(self):
        payload = {}
        self.assertFalse(record_delivery(payload, "000001", "test", "2026-10-23T10:30+09:00", "2026-10-23T10:00+09:00", "test", "test"))
        self.assertFalse(record_delivery(payload, "000001", "test", "2026-10-23T09:30", "2026-10-23T10:00+09:00", "test", "test"))


if __name__ == "__main__":
    unittest.main()
