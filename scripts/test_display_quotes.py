import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_candidate_monitor as monitor


class DisplayQuoteTests(unittest.TestCase):
    now = dt.datetime.fromisoformat("2026-10-16T15:23:00+09:00")

    def row(self, **changes):
        return {"itemCode": "274400", "closePrice": "2,365", "compareToPreviousClosePrice": "155",
                "compareToPreviousPrice": {"code": "2"}, "fluctuationsRatio": "7.01",
                "localTradedAt": "2026-10-16T15:22:57+09:00", **changes}

    def fetch(self, row):
        with patch.object(monitor, "fetch_bytes", return_value=json.dumps({"datas": [row]}).encode()) as fetch:
            quote = monitor.fetch_display_quote("274400", self.now)
            self.assertIn("/realtime/domestic/stock/274400", fetch.call_args.args[0])
            return quote

    def test_official_price_and_previous_close(self):
        quote = self.fetch(self.row())
        self.assertEqual((quote["price"], quote["previousClose"], quote["change"]), (2365, 2210, 7.01))
        self.assertEqual(quote["time"], "2026-10-16T15:22:57+09:00")

    def test_falling_flat_and_raw_fields(self):
        falling = self.fetch(self.row(compareToPreviousPrice={"code": "5"}, fluctuationsRatio="-6.15"))
        self.assertEqual(falling["previousClose"], 2520)
        flat = self.fetch(self.row(compareToPreviousPrice={"code": "3"}, compareToPreviousClosePrice="0", fluctuationsRatio="0"))
        self.assertEqual(flat["previousClose"], 2365)
        raw = self.fetch(self.row(closePrice="invalid", closePriceRaw="2365"))
        self.assertEqual(raw["price"], 2365)

    def test_reject_invalid_data(self):
        for changes in ({"itemCode": "000001"}, {"closePrice": "NaN"}, {"closePrice": "0"},
                        {"fluctuationsRatio": "-1.0"}, {"compareToPreviousPrice": {"code": "unknown"}},
                        {"localTradedAt": "2026-10-16T16:00:00+09:00"},
                        {"localTradedAt": "2026-10-16T15:22:00"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.fetch(self.row(**changes))

    def test_failed_refresh_retains_quote_time_and_analysis(self):
        quote = {"price": 2365, "time": "2026-10-16T15:22:00+09:00", "previousClose": 2210}
        candidate = {"code": "274400", "intraday": {"riseCount": 2}, "displayCharts": {"quote": quote.copy()}}
        with tempfile.TemporaryDirectory() as folder, patch.object(monitor, "fetch_display_chart", return_value={"series": []}), \
                patch.object(monitor, "fetch_display_quote", side_effect=OSError("unavailable")):
            monitor.refresh_display_charts({"candidates": [candidate]}, self.now, Path(folder) / "missing.json")
        self.assertEqual(candidate["displayCharts"]["quote"]["time"], quote["time"])
        self.assertEqual(candidate["displayCharts"]["quote"]["dataStatus"], "stale")
        self.assertEqual(candidate["intraday"], {"riseCount": 2})


if __name__ == "__main__":
    unittest.main()
