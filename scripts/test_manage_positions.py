from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from manage_positions import candidate_default_stop, close_position, register_position, trend_is_weak
from update_candidate_monitor import KST


class PositionManagerTests(unittest.TestCase):
    def test_register_and_close_position(self) -> None:
        payload = {"positions": []}
        current = dt.datetime(2026, 10, 5, 9, tzinfo=KST)
        register_position(payload, "005720", "넥센", 6490, 6150, 5, 3, current)
        position = payload["positions"][0]
        self.assertEqual(position["targetPrice"], 6814)
        self.assertEqual(position["status"], "open")
        close_position(payload, "005720", current)
        self.assertEqual(position["status"], "closed")

    def test_stop_price_must_be_below_buy_price(self) -> None:
        with self.assertRaises(ValueError):
            register_position(
                {"positions": []}, "005720", "넥센", 6490, 6500, 5,
                3, dt.datetime(2026, 10, 5, 9, tzinfo=KST),
            )

    def test_blank_stop_uses_three_percent_default(self) -> None:
        payload = {"positions": []}
        register_position(
            payload, "005720", "넥센", 6490, None, 5, 3,
            dt.datetime(2026, 10, 5, 9, tzinfo=KST),
        )
        position = payload["positions"][0]
        self.assertEqual(position["stopPrice"], 6295)
        self.assertEqual(position["stopSource"], "default_pct")

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
