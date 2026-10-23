import copy
import datetime as dt
import unittest
from unittest.mock import patch

from ah_rules import (KST, daily_series, daily_checks, daily_match, hybrid_rows,
                      review_alerts, remaining_days, message)
from update_ah_monitor import update, seed_signal_day


class AhRulesTests(unittest.TestCase):
    def rows(self):
        rows = [{"d": str(i), "m4": 100, "m60": 100} for i in range(150)]
        for offset in range(25, 46):
            rows[-1 - offset]["m60"] = 100 + offset
        for offset, value in ((2, 98), (1, 99), (0, 100)):
            rows[-1 - offset]["m60"] = value
        for offset in (10, 11, 12):
            rows[-1 - offset]["m4"] = 101
        return rows

    def test_exact_daily_offsets_and_trends(self):
        rows = self.rows()
        self.assertTrue(all(daily_checks(rows).values()))
        for offset, key in ((45, "A"), (2, "B"), (12, "C"), (5, "D"), (1, "E")):
            changed = copy.deepcopy(rows)
            if key in ("A", "B"):
                changed[-1 - offset]["m60"] = changed[-offset]["m60"]
            elif key == "D":
                changed[-1 - offset]["m4"] = 101
            else:
                changed[-1 - offset]["m4"] = 90
            self.assertFalse(daily_checks(changed)[key], key)

    def test_h_one_sided_and_daily_warmup(self):
        rows = self.rows()
        rows[-2]["m4"] = 150
        self.assertTrue(daily_checks(rows)["H"])
        self.assertIsNone(daily_checks(rows[:104]))

    def test_daily_sma_no_future_and_price_replacement(self):
        rows = [{"d": f"{i:04}", "c": 100} for i in range(150)]
        rows[-1]["c"] = 10000
        series = daily_series(rows, "0148")
        self.assertEqual(series[-1]["m4"], 100)
        daily_match(rows, "0149", 108)
        self.assertEqual(rows[-1]["c"], 10000)

    def test_native_prefix_only_and_actual_warmup(self):
        start = dt.datetime(2026, 10, 20, 9, tzinfo=KST)
        native = [{"t": (start + dt.timedelta(minutes=30*i)).isoformat(), "c": 100+i} for i in range(181)]
        daily = [{"d": "2026-10-19", "c": 100, "o": 80, "v": 1},
                 {"d": "2026-10-20", "c": 10000, "o": 10000, "v": 1}]
        rows = hybrid_rows(native, daily, start + dt.timedelta(days=5))
        self.assertEqual(rows[0]["estimated180Bars"], 179)
        self.assertIsNone(rows[0]["m180"])
        self.assertEqual(rows[-1]["estimated180Bars"], 0)
        self.assertAlmostEqual(rows[-1]["m180"], sum(range(101, 281))/180)
        self.assertEqual(native[0]["c"], rows[0]["c"])

    def signals(self):
        return [dict(t=f"2026-10-22T{hour}:00+09:00", c=100, complete=True,
                     m10=102, m120=value, m180=100, estimated10Bars=0,
                     estimated120Bars=0, estimated180Bars=20)
                for hour, value in (("13", 99), ("14", 101), ("15", 101))]

    def test_fresh_completed_cross_and_no_duplicate_enrollment(self):
        rows = self.signals()
        registered = "2026-10-22T14:30:00+09:00"
        candidate = {"code": "123456", "registeredAt": registered,
                     "alertCursor": "2026-10-22T14:29:59+09:00"}
        result = review_alerts(candidate, rows[:2], ["2026-10-22"], dt.datetime.fromisoformat(registered), True)
        self.assertEqual([a["kind"] for a in result], ["cross"])
        rows[1]["complete"] = False
        self.assertEqual(review_alerts(candidate, rows[:2], ["2026-10-22"], dt.datetime.fromisoformat(registered), True), [])

    def test_prior_trading_day_enrollment_and_broken_state(self):
        rows = self.signals()
        candidate = {"code": "123456", "registeredAt": "2026-10-23T09:10:00+09:00"}
        calendar = ["2026-10-21", "2026-10-22", "2026-10-23"]
        now = dt.datetime.fromisoformat(candidate["registeredAt"])
        alerts = review_alerts(candidate, rows, calendar, now, True)
        self.assertEqual(alerts[0]["kind"], "enrollment")
        rows[-1]["m10"] = 99
        self.assertEqual(review_alerts(candidate, rows, calendar, now, True), [])
        rows[-1]["m10"] = 102
        candidate["registeredAt"] = "2026-10-26T09:10:00+09:00"
        self.assertEqual(review_alerts(candidate, rows, calendar+["2026-10-26"], dt.datetime.fromisoformat(candidate["registeredAt"]), True), [])

    def test_weekend_and_five_trading_day_window(self):
        calendar = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
        self.assertEqual(remaining_days("2026-10-19", calendar), 1)
        self.assertEqual(remaining_days("2026-10-23", calendar), 5)
        self.assertEqual(remaining_days("2026-10-19", calendar+["2026-10-26"]), 0)

    def test_notification_percentage_and_approximation_label(self):
        row = self.signals()[-1]
        text = message({"code": "123456", "name": "Example"}, {"kind": "enrollment", "bar": row, "cross": row}, 80)
        self.assertIn("+25.00%", text)
        self.assertIn("근사", text)
        self.assertIn("view=ah", text)


class AhDriverTests(unittest.TestCase):
    def fixture(self):
        now = dt.datetime(2026, 10, 23, 14, 40, tzinfo=KST)
        dates = [(now.date()-dt.timedelta(days=i)).isoformat() for i in range(180, -1, -1)]
        market = {"dates": dates, "stocks": [{"c": "123456", "n": "Example", "m": "KOSDAQ", "s": 0,
                                               "p": [100]*len(dates), "v": [1]*len(dates)}]}
        rows = AhRulesTests().signals()
        charts = {"daily": {"history": [{"d": d, "c": 100, "v": 1} for d in dates]},
                  "intraday": {"series": rows, "history": rows},
                  "quote": {"price": 100, "time": now.isoformat()}}
        return now, market, charts

    @patch("update_ah_monitor.daily_match", return_value=True)
    @patch("update_ah_monitor.potential_match", return_value=True)
    def test_bootstrap_suppresses_history_then_no_registration_reset(self, *_):
        now, market, charts = self.fixture()
        sender = unittest.mock.Mock()
        with patch("update_ah_monitor.seed_signal_day", return_value=market["dates"][-1]):
            payload = update({}, market, now, refresh_fn=lambda *_: copy.deepcopy(charts), sender=sender)
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertFalse(payload["candidates"][0]["enrollmentPending"])
        self.assertFalse(sender.called)
        registered = payload["candidates"][0]["registeredAt"]
        payload = update(payload, market, now+dt.timedelta(minutes=10), no_notify=True,
                         refresh_fn=lambda *_: copy.deepcopy(charts), sender=sender)
        self.assertEqual(payload["candidates"][0]["registeredAt"], registered)

    @patch("update_ah_monitor.daily_match", return_value=True)
    def test_initial_seed_does_not_relabel_old_continuous_match(self, *_):
        daily = [{"d": day, "c": 100} for day in ("2026-10-16", "2026-10-19", "2026-10-20")]
        self.assertIsNone(seed_signal_day(daily, [], ["2026-10-19", "2026-10-20"]))

    @patch("update_ah_monitor.daily_match", return_value=True)
    @patch("update_ah_monitor.potential_match", return_value=True)
    def test_expired_candidate_only_reenters_after_a_real_break(self, *_):
        now, market, charts = self.fixture()
        old = {"code": "123456", "name": "Example", "dailySignalDate": "2026-10-10", "registeredAt": "2026-10-10T10:00:00+09:00"}
        for last_matched in (True, False):
            payload = {"initializedAt": now.isoformat(), "candidates": [copy.deepcopy(old)],
                       "registry": {"123456": {"lastMatched": last_matched}}}
            result = update(payload, market, now, no_notify=True, refresh_fn=lambda *_: copy.deepcopy(charts))
            self.assertEqual(len(result["candidates"]), 0 if last_matched else 1)
            if not last_matched:
                self.assertEqual(result["candidates"][0]["registeredAt"], now.isoformat(timespec="seconds"))

    @patch("update_ah_monitor.daily_match", return_value=True)
    @patch("update_ah_monitor.potential_match", return_value=True)
    @patch.dict("os.environ", {"NTFY_TOPIC": "test-do-not-send"})
    def test_failed_send_preserves_retry_and_ack_records_once(self, *_):
        now, market, charts = self.fixture()
        initial = {"initializedAt": now.isoformat(), "candidates": []}
        broken = unittest.mock.Mock(side_effect=RuntimeError("failed"))
        payload = update(initial, market, now, refresh_fn=lambda *_: copy.deepcopy(charts), sender=broken)
        self.assertTrue(payload["candidates"][0]["enrollmentPending"])
        self.assertFalse(payload.get("notificationHistory"))
        sender = unittest.mock.Mock()
        payload = update(payload, market, now+dt.timedelta(minutes=10), refresh_fn=lambda *_: copy.deepcopy(charts), sender=sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(len(payload["notificationHistory"]), 1)
        payload = update(payload, market, now+dt.timedelta(minutes=20), refresh_fn=lambda *_: copy.deepcopy(charts), sender=sender)
        self.assertEqual(sender.call_count, 1)


if __name__ == "__main__":
    unittest.main()
