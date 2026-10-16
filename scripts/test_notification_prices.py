import copy
import datetime as dt
import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
import manage_positions as positions
from notification_prices import price_with_day_change


class NotificationPriceTests(unittest.TestCase):
    quote = {"price": 2400, "previousClose": 2210, "time": "2026-10-16T10:05:00+09:00"}
    stamp = "2026-10-16T09:30:00+09:00"

    def test_signal_price_not_later_quote_price(self):
        self.assertEqual(price_with_day_change(2365, self.stamp, self.quote), "2,365원 · 전일 대비 +7.01%")
        self.assertEqual(price_with_day_change(2100, self.stamp, self.quote), "2,100원 · 전일 대비 -4.98%")
        self.assertEqual(price_with_day_change(2210, self.stamp, self.quote), "2,210원 · 전일 대비 +0.00%")

    def test_same_kst_session_including_aftermarket(self):
        self.assertIn("+7.01%", price_with_day_change(2365, "2026-10-16T19:00+09:00", self.quote))
        self.assertIn("+7.01%", price_with_day_change(2365, "2026-10-16T00:30:00Z", self.quote))
        self.assertIn("확인 불가", price_with_day_change(2365, "2026-10-15T09:30+09:00", self.quote))

    def test_missing_or_invalid_baseline_is_not_guessed(self):
        for quote in ({}, None, {**self.quote, "previousClose": 0}, {**self.quote, "previousClose": float("nan")},
                      {**self.quote, "previousClose": True}, {**self.quote, "time": "invalid"}):
            with self.subTest(quote=quote):
                self.assertEqual(price_with_day_change(2365, self.stamp, quote), "2,365원 · 전일 대비 확인 불가")

    def response(self):
        response = Mock(status=200)
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_all_chart_alert_messages_without_sending(self):
        row = {"t": self.stamp, "c": 2365, "m3": 2360, "m10": 2350, "m20": 2340, "m40": 2330, "m60": 2320}
        candidate = {"name": "이노시뮬레이션", "code": "274400", "displayCharts": {
            "quote": self.quote, "daily": {"history": []}, "intraday": {"series": [row], "history": [row]}}}
        state = {"stage": "primary", "ruleName": "test", "clusterLines": [3, 10, 20, 40], "riseCount": 1,
                 "clusterSpreadPercent": .5, "completedAt": "2026-10-16T10:00+09:00", "spread": .5,
                 "previousMa3": 2300, "firstMa3": 2320, "secondMa3": 2360}
        before = copy.deepcopy(candidate)
        for kind in ("imageMa3Rise", "imageMa3Breakout", "imageMa10Breakout", "coreMorningMa3",
                     "recoveryMa10Breakout", "purple", "compact", "convergenceBreakout"):
            with self.subTest(kind=kind), patch.object(colors, "chart_image", return_value=b"test"), \
                    patch.object(colors.urllib.request, "urlopen", return_value=self.response()) as opened:
                colors.send("test-only", candidate, {"candidates": [candidate]}, row, state, kind,
                            dt.datetime.fromisoformat("2026-10-16T10:05+09:00"))
                message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
                self.assertIn("2,365원 · 전일 대비 +7.01%", message)
        self.assertEqual(candidate, before, "Message formatting must not change signal state")

    def test_holdings_day_change_is_distinct_from_investment_return(self):
        position = {"code": "274400", "name": "이노시뮬레이션", "lastPrice": 2365, "lastPriceTime": self.stamp,
                    "buyPrice": 2000, "returnPct": 18.25, "stopPrice": 1900, "targetPrice": 2300}
        with patch.object(positions, "fetch_display_quote", return_value=self.quote), \
                patch.object(positions.urllib.request, "urlopen", return_value=self.response()) as opened:
            positions.send_alert("test-only", position, "target")
        message = json.loads(opened.call_args.args[0].data)["message"]
        self.assertIn("현재 확인가격: 2,365원 · 전일 대비 +7.01%", message)
        self.assertIn("수익률: +18.25%", message)
        with patch.object(positions, "fetch_display_quote", side_effect=OSError("offline")), \
                patch.object(positions.urllib.request, "urlopen", return_value=self.response()) as opened:
            positions.send_alert("test-only", position, "weak")
        self.assertIn("전일 대비 확인 불가", json.loads(opened.call_args.args[0].data)["message"])


if __name__ == "__main__":
    unittest.main()
