from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from manage_positions import (
    apply_stop_limit,
    candidate_default_stop,
    close_position,
    register_position,
    stop_confirmation_ready,
    trend_is_weak,
)
from update_candidate_monitor import KST


class PositionManagerTests(unittest.TestCase):
    def test_register_and_close_position(self) -> None:
        payload = {"positions": []}
        current = dt.datetime(2026, 10, 5, 9, tzinfo=KST)
        register_position(payload, "005720", "넥센", 6490, 10, 6150, 5, 3, current)
        position = payload["positions"][0]
        self.assertEqual(position["targetPrice"], 6814)
        self.assertEqual(position["status"], "open")
        close_position(payload, "005720", 6800, 10, current)
        self.assertEqual(position["status"], "closed")
        self.assertEqual(position["sellPrice"], 6800)
        self.assertEqual(position["realizedProfitPerShare"], 310)
        self.assertEqual(position["realizedProfitTotal"], 3100)
        self.assertEqual(position["realizedReturnPct"], 4.78)

    def test_sell_price_must_be_positive(self) -> None:
        payload = {"positions": []}
        current = dt.datetime(2026, 10, 5, 9, tzinfo=KST)
        register_position(payload, "005720", "넥센", 6490, 10, 6150, 5, 3, current)
        with self.assertRaises(ValueError):
            close_position(payload, "005720", 0, 10, current)

    def test_sell_price_can_complete_legacy_closed_position(self) -> None:
        payload = {
            "positions": [{
                "code": "005720", "name": "넥센", "buyPrice": 6480,
                "status": "closed", "closedAt": "2026-10-04T15:11+09:00",
            }]
        }
        current = dt.datetime(2026, 10, 5, 9, tzinfo=KST)
        close_position(payload, "005720", 6700, 10, current)
        position = payload["positions"][0]
        self.assertEqual(position["sellPrice"], 6700)
        self.assertEqual(position["realizedReturnPct"], 3.4)
        self.assertEqual(position["realizedProfitTotal"], 2200)
        self.assertEqual(position["closedAt"], "2026-10-04T15:11+09:00")

    def test_stop_price_must_be_below_buy_price(self) -> None:
        with self.assertRaises(ValueError):
            register_position(
                {"positions": []}, "005720", "넥센", 6490, 10, 6500, 5,
                3, dt.datetime(2026, 10, 5, 9, tzinfo=KST),
            )

    def test_blank_stop_uses_five_percent_maximum_loss(self) -> None:
        payload = {"positions": []}
        register_position(
            payload, "005720", "넥센", 6490, 10, None, 5, 5,
            dt.datetime(2026, 10, 5, 9, tzinfo=KST),
        )
        position = payload["positions"][0]
        self.assertEqual(position["stopPrice"], 6166)
        self.assertEqual(position["stopSource"], "max_loss_pct")
        self.assertFalse(position["riskWarning"])

    def test_distant_technical_stop_is_capped_at_five_percent(self) -> None:
        stop, technical, capped = apply_stop_limit(6480, 5430, 5)
        self.assertEqual(stop, 6156)
        self.assertEqual(technical, 5430)
        self.assertTrue(capped)

    def test_near_technical_stop_is_kept(self) -> None:
        stop, technical, capped = apply_stop_limit(6480, 6300, 5)
        self.assertEqual(stop, 6300)
        self.assertEqual(technical, 6300)
        self.assertFalse(capped)

    def test_stop_is_confirmed_only_after_1500_on_same_trading_day(self) -> None:
        before_close = dt.datetime(2026, 10, 5, 14, 59, tzinfo=KST)
        at_close = dt.datetime(2026, 10, 5, 15, 0, tzinfo=KST)
        self.assertFalse(stop_confirmation_ready(before_close, before_close))
        self.assertTrue(stop_confirmation_ready(at_close, at_close))
        self.assertFalse(stop_confirmation_ready(
            at_close, dt.datetime(2026, 10, 2, 15, 30, tzinfo=KST)
        ))

    def test_candidate_previous_close_is_default_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(
                json.dumps({"candidates": [{"code": "005720", "daily": {"preSpikeClose": 6100}}]}),
                encoding="utf-8",
            )
            self.assertEqual(candidate_default_stop("005720", path), 6100)

    def test_trend_weak_requires_three_ma20_declines_and_close_below(self) -> None:
        bars = [
            {"ma20": value, "close": close}
            for value, close in ((100, 101), (99, 100), (98, 99), (97, 96))
        ]
        self.assertTrue(trend_is_weak(bars))
        bars[-1]["close"] = 98
        self.assertFalse(trend_is_weak(bars))


if __name__ == "__main__":
    unittest.main()
