import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_candidate_monitor as monitor


class CurrentSearchRegistrationTests(unittest.TestCase):
    def test_initial_dates_corrected_once_without_touching_holdings(self):
        old = {"code": "009830", "dailySignalDate": "2026-09-30", "registeredAt": "2026-10-04T12:00:00+09:00", "registrationPrice": 36100, "registrationSource": "first_persisted_snapshot", "daily": {"close": 33100}}
        new = {"code": "001440", "dailySignalDate": "2026-10-08", "registeredAt": "2026-10-08T17:00:00+09:00", "registrationSource": "registration_reference_close"}
        paper = {"positions": [{"code": "009830", "status": "open", "buyPrice": 35000}]}
        payload = {"candidates": [old, new], "paperTrading": paper}
        self.assertEqual(monitor.correct_initial_registration_dates(payload), 1)
        self.assertEqual(old["registeredAt"], "2026-09-30T16:40:00+09:00")
        self.assertEqual(old["registrationPrice"], 33100)
        self.assertEqual(old["originalRegistrationPrice"], 36100)
        self.assertEqual(monitor.correct_initial_registration_dates(payload), 0)
        self.assertEqual(new["registeredAt"], "2026-10-08T17:00:00+09:00")
        self.assertEqual(payload["paperTrading"], paper)

    def build(self, match_offset, previous=None, stale=False):
        today = dt.datetime(2026, 10, 8, 17, tzinfo=monitor.KST)
        dates = [(today.date() - dt.timedelta(days=30 - i)).isoformat() for i in range(31)]
        if stale:
            dates = dates[:-1]
        rows = [{"date": day, "close": 100, "volume": 10} for day in dates]
        payload = {"dates": dates, "stocks": [{"c": "001440", "n": "test", "m": "KOSPI", "v": []}]}
        result = {"candidateTier": "core"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stocks.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(monitor, "now_kst", return_value=today), patch.object(monitor, "stock_rows", return_value=rows), patch.object(monitor, "evaluate_ag", side_effect=lambda data, index: result if index == len(rows) - 1 - match_offset else None):
                return monitor.build_daily_candidates(path, previous or {})

    def test_does_not_backfill_missed_previous_day(self):
        self.assertEqual(self.build(1)["candidates"], [])

    def test_registers_current_result_now(self):
        candidate = self.build(0)["candidates"][0]
        self.assertEqual(candidate["dailySignalDate"], "2026-10-08")
        self.assertTrue(candidate["registeredAt"].startswith("2026-10-08"))

    def test_stale_data_cannot_create_new_candidate(self):
        self.assertEqual(self.build(0, stale=True)["candidates"], [])

    def test_existing_registration_is_preserved(self):
        old = {"code": "001440", "dailySignalDate": "2026-10-07", "registeredAt": "2026-10-07T17:00:00+09:00", "registrationPrice": 100}
        candidate = self.build(1, {"candidates": [old]})["candidates"][0]
        self.assertEqual(candidate["registeredAt"], old["registeredAt"])

    def test_window_uses_registration_not_old_ag_date(self):
        calendar = ["2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
        candidate = {"code": "001440", "dailySignalDate": "2026-09-15", "registeredAt": "2026-10-01T17:00:00+09:00"}
        position = {"code": "001440", "status": "open"}
        payload = {"candidates": [candidate], "paperTrading": {"positions": [position]}}
        monitor.expire_registration_window(payload, calendar, "2026-10-08")
        self.assertEqual(candidate["tradingDaysRemaining"], 1)
        self.assertEqual(len(payload["candidates"]), 1)
        monitor.expire_registration_window(payload, calendar, "2026-10-09")
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["history"][0]["archiveReason"], "window_completed")
        self.assertEqual(payload["paperTrading"]["positions"], [position])


if __name__ == "__main__":
    unittest.main()
