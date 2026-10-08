import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_candidate_monitor as monitor


class CurrentSearchRegistrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
