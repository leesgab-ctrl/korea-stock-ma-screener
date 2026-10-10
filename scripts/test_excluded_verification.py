import datetime as dt
import unittest
from unittest.mock import patch

import update_candidate_monitor as monitor


class ExcludedVerificationTests(unittest.TestCase):
    def test_native_daily_zero_volume_keeps_close_without_rejecting_chart(self):
        import json
        response = {"stockExchangeType": "KRX", "priceInfos": [
            {"localDate": "20261007", "openPrice": 650, "highPrice": 670, "lowPrice": 640, "closePrice": 666, "accumulatedTradingVolume": 100},
            {"localDate": "20261008", "openPrice": 0, "highPrice": 0, "lowPrice": 0, "closePrice": 666, "accumulatedTradingVolume": 0},
        ]}
        with patch.object(monitor, "fetch_bytes", return_value=json.dumps(response).encode()):
            chart = monitor.fetch_display_chart("215790", "day", dt.datetime.fromisoformat("2026-10-10T12:00:00+09:00"))
        self.assertEqual(chart["dataStatus"], "ok")
        self.assertEqual(chart["barCount"], 2)
        self.assertTrue(chart["series"][-1]["noTrading"])
        self.assertEqual(chart["series"][-1]["o"], 666)

    def test_collects_until_window_completed_without_reactivating(self):
        calendar = [f"2026-09-{day:02}" for day in range(1, 12)]
        record = {"code": "000001", "registeredAt": "2026-09-01T17:00:00+09:00", "archiveReason": "manual_excluded", "archivedAt": "2026-09-02"}
        payload = {"history": [record], "candidates": []}
        def fetch(code, timeframe, current, previous):
            key = "d" if timeframe == "day" else "t"
            rows = [{key: day if key == "d" else day + "T15:00:00+09:00", "c": 100} for day in calendar]
            return {"history": rows, "series": rows, "dataStatus": "ok"}
        with patch.object(monitor, "fetch_display_chart", side_effect=fetch) as mocked:
            monitor.refresh_excluded_verification(payload, dt.datetime.fromisoformat("2026-09-05T18:00:00+09:00"), calendar)
            self.assertEqual(record["verificationStatus"], "collecting")
            monitor.refresh_excluded_verification(payload, dt.datetime.fromisoformat("2026-09-11T15:00:00+09:00"), calendar)
            self.assertEqual(record["verificationStatus"], "collecting")
            monitor.refresh_excluded_verification(payload, dt.datetime.fromisoformat("2026-09-11T20:01:00+09:00"), calendar)
            self.assertEqual(record["verificationEndDate"], "2026-09-11")
            self.assertEqual(record["verificationStatus"], "completed")
            calls = mocked.call_count
            monitor.refresh_excluded_verification(payload, dt.datetime.fromisoformat("2026-09-12T20:01:00+09:00"), calendar)
            self.assertEqual(mocked.call_count, calls)
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(record["archivedAt"], "2026-09-02")


if __name__ == "__main__":
    unittest.main()
