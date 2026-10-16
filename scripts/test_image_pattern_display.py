import datetime as dt
import unittest

import image_pattern_search as search


class ImagePatternDisplayTests(unittest.TestCase):
    def setUp(self):
        self.rule = {"id": "test120", "version": 1, "type": "ma3_ma120_recovery", "enabled": True,
                     "imageIds": ["image120"], "name": "MA120 이탈후 재돌파", "description": "test"}
        self.rows = [{"t": f"2026-10-16T{9+i//2:02d}:{'30' if i%2 else '00'}+09:00", "m3": value,
                      "m120": 100, "c": 103, "complete": True} for i, value in enumerate([102, 99, 101, 101])]
        self.candidate = {"code": "000001", "name": "Test", "registeredAt": "2026-10-15T10:00+09:00", "displayCharts": {
            "intraday": {"series": self.rows, "dataStatus": "ok"},
            "quote": {"price": 150, "previousClose": 100, "time": "2026-10-16T15:00+09:00"},
            "daily": {"history": [{"d": "2026-10-15", "c": 50}]}}}
        self.payload = {"candidates": [self.candidate]}
        self.board = {"version": 1, "records": [{"id": "image120", "conditionConfirmed": True,
                     "conditionConfirmedAt": "2026-10-16T08:00+09:00"}]}

    def process(self, day="2026-10-16"):
        search.process(self.payload, dt.datetime.fromisoformat(day + "T16:00+09:00"), no_notify=True,
                       board=self.board, rules=[self.rule])
        return self.payload["imageSearch"]["matches"][0]

    def test_capture_price_not_latest_price(self):
        event = self.process()
        self.assertEqual(event["changePct"], 3)
        self.assertEqual(event["previousClose"], 100)

    def test_preserve_capture_baseline_across_days_and_bar_revisions(self):
        self.process()
        self.candidate["displayCharts"]["quote"] = {"price": 210, "previousClose": 200, "time": "2026-10-17T15:00+09:00"}
        self.assertEqual(self.process("2026-10-17")["changePct"], 3)
        self.rows[-1]["c"] = 105
        self.assertEqual(self.process("2026-10-17")["changePct"], 5)

    def test_corrected_same_day_official_reference_takes_priority(self):
        self.process()
        self.candidate["displayCharts"]["quote"]["previousClose"] = 102
        self.assertAlmostEqual(self.process()["changePct"], 0.980392, places=6)

    def test_unknown_baseline_not_guessed_from_inconsistent_daily_chart(self):
        self.candidate["displayCharts"].pop("quote")
        event = self.process()
        self.assertIsNone(event["changePct"])
        self.assertIsNone(event["previousClose"])


if __name__ == "__main__":
    unittest.main()
