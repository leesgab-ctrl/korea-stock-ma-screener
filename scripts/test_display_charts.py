import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import update_candidate_monitor as monitor


class DisplayChartTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 7, 15, 2, tzinfo=monitor.KST)

    def fetch(self, rows, timeframe="minute30", previous=None):
        response = {"stockExchangeType": "KRX", "priceInfos": rows}
        with mock.patch.object(monitor, "fetch_bytes", return_value=json.dumps(response).encode()):
            return monitor.fetch_display_chart("007810", timeframe, self.now, previous)

    def row(self, stamp, close=100):
        return {"localDateTime": stamp, "openPrice": close - 1, "highPrice": close + 2,
                "lowPrice": close - 2, "currentPrice": close, "accumulatedTradingVolume": 37}

    def test_native_prices_volume_and_forming_bar(self):
        chart = self.fetch([self.row("20261007143000"), self.row("20261007150000", 102)])
        self.assertEqual(chart["series"][0]["v"], 37)
        self.assertEqual(chart["series"][1]["v"], 37)
        self.assertEqual(chart["series"][1]["o"], 101)
        self.assertTrue(chart["series"][0]["complete"])
        self.assertFalse(chart["series"][1]["complete"])

    def test_five_sessions_preserve_hidden_warmup(self):
        rows = []
        for day in (28, 29, 30):
            for slot in range(22):
                stamp = dt.datetime(2026, 9, day, 9) + dt.timedelta(minutes=slot * 30)
                rows.append(self.row(stamp.strftime("%Y%m%d%H%M%S")))
        for day in (1, 2, 6, 7):
            for slot in range(12):
                stamp = dt.datetime(2026, 10, day, 9) + dt.timedelta(minutes=slot * 30)
                rows.append(self.row(stamp.strftime("%Y%m%d%H%M%S")))
        chart = self.fetch(rows)
        self.assertEqual(len({row["t"][:10] for row in chart["series"]}), 7)
        self.assertEqual(chart["series"][-1]["m60"], 100)
        self.assertEqual(len(chart["history"]), len(rows))

    def test_invalid_venue_and_nonfinite_prices(self):
        with mock.patch.object(monitor, "fetch_bytes", return_value=b'{"stockExchangeType":"NXT","priceInfos":[]}'):
            with self.assertRaises(ValueError):
                monitor.fetch_display_chart("007810", "minute30", self.now)
        with self.assertRaises(ValueError):
            self.fetch([self.row("20261007143000", float("nan"))])

    def test_only_native_cache_and_authoritative_replacement(self):
        prior = self.fetch([self.row("20261006140000"), self.row("20261007140000"), self.row("20261007143000")])
        mixed = {**prior, "source": "regular_reconstructed"}
        self.assertEqual(len(self.fetch([self.row("20261007143000")], previous=mixed)["history"]), 1)
        chart = self.fetch([self.row("20261007133000"), self.row("20261007143000", 104)], previous=prior)
        self.assertEqual(len(chart["history"]), 3)
        self.assertFalse(any("10-07T14:00" in row["t"] for row in chart["history"]))
        self.assertEqual(chart["series"][-1]["c"], 104)

    def test_daily_ma10(self):
        rows = [{"localDate": f"202609{day:02}", "openPrice": 100, "highPrice": 130,
                 "lowPrice": 90, "closePrice": 100 + day, "accumulatedTradingVolume": 1000}
                for day in range(1, 21)]
        chart = self.fetch(rows, "day")
        self.assertEqual(chart["series"][9]["m10"], 105.5)

    def test_refresh_failure_preserves_analysis(self):
        payload = {"candidates": [{"code": "007810", "status": "signal", "intraday": {"riseCount": 5},
                    "displayCharts": {"intraday": {"series": [{"c": 100}]}}}]}
        with mock.patch.object(monitor, "fetch_display_chart", side_effect=ValueError("bad source")):
            monitor.refresh_display_charts(payload, self.now)
        self.assertEqual(payload["candidates"][0]["intraday"], {"riseCount": 5})
        self.assertEqual(payload["candidates"][0]["status"], "signal")
        self.assertEqual(payload["candidates"][0]["displayCharts"]["intraday"]["dataStatus"], "stale")

    def test_charts_only_never_enriches_or_notifies(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidate.json"
            original = {"candidates": [{"code": "007810", "status": "signal"}], "notifiedSignals": ["saved"]}
            path.write_text(json.dumps(original))
            args = mock.Mock(mode="charts", output=str(path), now=self.now.isoformat(), github_output=None)
            with mock.patch.object(monitor, "parse_args", return_value=args), mock.patch.object(monitor, "enrich") as enrich, mock.patch.object(monitor, "refresh_display_charts") as refresh:
                monitor.main()
            enrich.assert_not_called()
            refresh.assert_called_once()
            self.assertEqual(json.loads(path.read_text()), original)

    def test_holdings_survive_candidate_expiry_and_do_not_change_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "positions.json"
            content = json.dumps({"positions": [{"code": "199800", "name": "held", "status": "open"},
                                                {"code": "007810", "name": "closed", "status": "closed"}]})
            path.write_text(content)
            payload = {"candidates": []}
            with mock.patch.object(monitor, "fetch_display_chart", return_value={"series": [{"c": 100}], "dataStatus": "ok"}) as fetch:
                monitor.refresh_display_charts(payload, self.now, path)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(list(payload["holdingCharts"]), ["199800"])
            self.assertEqual(path.read_text(), content)
            with mock.patch.object(monitor, "fetch_display_chart") as fetch:
                monitor.refresh_display_charts({"candidates": [{"code": "199800"}]}, self.now, path)
            self.assertEqual(fetch.call_count, 2)

    def test_phase_shallow_deep_and_new_cycle(self):
        def row(m20, m3, complete=True):
            return dict(m20=m20, m3=m3, m40=100, m60=90, complete=complete)
        series = [row(105, 103), row(98, 97), row(99, 101), row(102, 104),
                  row(88, 87), row(89, 91), row(91, 93), row(87, 89, complete=False)]
        monitor.apply_display_phases(series)
        self.assertEqual([row["phase"] for row in series],
                         ["before", "pullback", "fast", "confirmed", "pullback", "fast", "confirmed", "confirmed"])
        self.assertEqual([row["phaseTarget"] for row in series[1:]], [40, 40, 40, 60, 60, 60, 60])
