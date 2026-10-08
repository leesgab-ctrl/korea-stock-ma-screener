import datetime as dt
import unittest
from paper_trading import evaluate, update_paper


class PaperTests(unittest.TestCase):
    def bar(self, stamp, **values):
        return {"t": stamp, "complete": True, "o": 100, "h": 101, "l": 99, "c": 100,
                "m3": 100, "m10": 100, "m20": 102, "m40": 101, "m60": 100, **values}

    def candidate(self, bars):
        return {"code": "000001", "name": "test", "dailySignalDate": "2026-10-01",
                "daily": {"spikeDate": "2026-10-01", "preSpikeLow": 90},
                "displayCharts": {"intraday": {"dataStatus": "ok", "series": bars, "history": bars}}}

    def test_low_break(self):
        bars = [self.bar("2026-10-02T09:00:00+09:00"), self.bar("2026-10-02T09:30:00+09:00", c=89)]
        self.assertEqual(evaluate(self.candidate(bars))["excludedReason"], "대량거래 전일 저가 이탈")

    def test_ma60_depth_not_exclusion(self):
        bars = [self.bar("2026-10-02T09:00:00+09:00"), self.bar("2026-10-02T09:30:00+09:00", m20=98)]
        result = evaluate(self.candidate(bars))
        self.assertIsNone(result["excludedReason"])
        self.assertEqual(result["group"], "target")

    def test_bootstrap_does_not_buy(self):
        now = dt.datetime.fromisoformat("2026-10-08T15:00:00+09:00")
        payload = {"candidates": [self.candidate([])]}
        update_paper(payload, now)
        self.assertEqual(payload["paperTrading"]["positions"], [])

    def test_recovery_requires_next_bar(self):
        values = [102, 99, 98, 98.5, 99, 99.5, 100, 101.1, 101.2]
        bars = [self.bar(f"2026-10-02T{9 + i // 2:02d}:{30 * (i % 2):02d}:00+09:00", m20=v, m60=97, c=103)
                for i, v in enumerate(values)]
        self.assertEqual(evaluate(self.candidate(bars[:-1]))["events"], [])
        events = evaluate(self.candidate(bars))["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], bars[-1]["t"])
        self.assertEqual(events[0]["type"], "recovery")

    def test_ambiguous_is_conservative(self):
        now = dt.datetime.fromisoformat("2026-10-08T15:00:00+09:00")
        bars = [self.bar("2026-10-08T10:00:00+09:00", h=106, l=94)]
        position = {"code": "000001", "status": "open", "buyPrice": 100, "stopPrice": 95,
                    "targetPrice": 105, "openedAt": "2026-10-08T09:30:00+09:00", "maxDrawdownPct": 0}
        payload = {"candidates": [self.candidate(bars)], "paperTrading": {"positions": [position], "seen": [], "startedAt": now.isoformat()}}
        update_paper(payload, now)
        self.assertEqual(position["exitReason"], "ambiguous")
        self.assertEqual(position["sellPrice"], 95)


if __name__ == "__main__":
    unittest.main()
