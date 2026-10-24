import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from first_rise_rules import KST, VERSION, daily_context, detect, remaining_days, closing_confirmation
from update_first_rise_monitor import update
from common_monitor import load_first_rise_candidates, merge_candidates
from image_pattern_search import process
from test_image_pattern_search import fixture, at


def sample():
    day = "2026-10-23"
    start = dt.datetime(2026, 9, 1, tzinfo=KST)
    daily = [{"d": (start+dt.timedelta(days=i)).date().isoformat(), "c": 120-i/2, "v": 100}
             for i in range(30)]
    end = dt.datetime.fromisoformat(day+"T13:00+09:00")
    rows = [{"t": (end-dt.timedelta(minutes=30*(80-i))).isoformat(timespec="minutes"),
             "o": 100, "c": 100, "h": 100, "l": 100, "v": 10} for i in range(81)]
    rows[-1].update(c=110, h=110, v=1000)
    current = end+dt.timedelta(minutes=30)
    return daily, rows, current


class FirstRiseRulesTests(unittest.TestCase):
    def test_all_conditions_and_volume_are_mandatory(self):
        daily, rows, current = sample()
        event = detect(rows, daily, "2026-10-23", current)[0]
        self.assertGreater(event["dailyVolumeMultiple"], 3)
        self.assertGreater(event["barVolumeMultiple"], 3)
        rows[-1]["v"] = 31
        self.assertEqual(detect(rows, daily, "2026-10-23", current), [])
        rows[-1]["v"] = 1000
        rows[-1]["o"] = 111
        self.assertEqual(detect(rows, daily, "2026-10-23", current), [])

    def test_no_future_candle_or_closing_volume_lookahead(self):
        daily, rows, current = sample()
        self.assertEqual(detect(rows, daily, "2026-10-23", current-dt.timedelta(seconds=1)), [])
        rows[-1]["v"] = 31
        daily.append({"d": "2026-10-23", "c": 9999, "v": 100000000})
        rows.append({**rows[-1], "t": (current+dt.timedelta(minutes=30)).isoformat(), "v": 100000000})
        self.assertEqual(detect(rows, daily, "2026-10-23", current), [])

    def test_previously_above_upper_is_not_a_fresh_cross(self):
        daily, rows, current = sample()
        rows[-2]["c"] = 101
        self.assertEqual(detect(rows, daily, "2026-10-23", current), [])

    def test_prior_rise_exclusion_and_missing_history(self):
        daily, rows, current = sample()
        daily[-1]["c"] = daily[-2]["c"]*1.08
        self.assertEqual(detect(rows, daily, "2026-10-23", current), [])
        self.assertIsNone(daily_context(daily[:24], "2026-10-23"))
        self.assertEqual(detect(rows[-65:], sample()[0], "2026-10-23", current), [])

    def test_registration_window(self):
        self.assertEqual(remaining_days("2026-10-19", ["2026-10-19", "2026-10-20"]), 9)
        self.assertEqual(remaining_days("2026-10-19", [f"2026-10-{i}" for i in range(19, 30)]), 0)

    def test_close_confirmation_requires_final_day_price_and_after_2030(self):
        daily = [{"d": f"2026-10-{day:02d}", "c": 100} for day in range(13, 23)] + [{"d": "2026-10-23", "c": 101}]
        self.assertIsNone(closing_confirmation(daily, "2026-10-23", dt.datetime.fromisoformat("2026-10-23T20:29:59+09:00")))
        current = dt.datetime.fromisoformat("2026-10-23T20:30:00+09:00")
        self.assertTrue(closing_confirmation(daily, "2026-10-23", current)["qualified"])
        self.assertLess(closing_confirmation(daily, "2026-10-23", current)["changePct"], 5)
        daily[-1]["c"] = 100
        self.assertFalse(closing_confirmation(daily, "2026-10-23", current)["qualified"])
        self.assertIsNone(closing_confirmation(daily[:-1], "2026-10-23", current))
        daily[-1]["c"] = float("nan")
        self.assertIsNone(closing_confirmation(daily, "2026-10-23", current))

    def test_daily_cross_is_fresh_not_an_already_above_state(self):
        current = dt.datetime.fromisoformat("2026-10-23T20:30+09:00")
        rows = [{"d": f"2026-10-{13+i:02d}", "c": price} for i, price in enumerate([100]*8+[110, 110, 130])]
        result = closing_confirmation(rows, "2026-10-23", current)
        self.assertGreater(result["changePct"], 5)
        self.assertGreater(result["ma3"], result["ma10"])
        self.assertFalse(result["qualified"])
        self.assertIsNone(closing_confirmation(rows[-10:], "2026-10-23", current))

    def test_cross_does_not_require_a_minimum_daily_gain(self):
        prices = [104]*7+[90, 110, 110, 109]
        rows = [{"d": f"2026-10-{13+i:02d}", "c": price} for i, price in enumerate(prices)]
        result = closing_confirmation(rows, "2026-10-23", dt.datetime.fromisoformat("2026-10-23T20:30+09:00"))
        self.assertTrue(result["qualified"])
        self.assertLess(result["changePct"], 0)

    def test_reference_examples_with_offline_native_data(self):
        root = Path(__file__).resolve().parents[1]/"outputs"
        for code, day, time in [("138360", "2026-10-20", "14:00"), ("300120", "2026-10-21", "13:30")]:
            minute = root/f"current-long-ma-pullback/charts/{code}.json"
            daily_path = root/f"daily-long-ma-pullback/charts/{code}.json"
            if not minute.exists() or not daily_path.exists():
                self.skipTest("Offline research caches are not deployed")
            rows = json.loads(minute.read_text(encoding="utf8"))["history"]
            daily = json.loads(daily_path.read_text(encoding="utf8"))["history"]
            current = dt.datetime.fromisoformat(day+"T20:30+09:00")
            events = detect(rows, daily, day, current)
            self.assertEqual(events[0]["completedAt"][11:16], time)
            self.assertGreaterEqual(events[0]["dailyVolumeMultiple"], 3)
            self.assertTrue(closing_confirmation(daily, day, current)["qualified"])


class FirstRiseMonitorTests(unittest.TestCase):
    def setup_data(self):
        daily, rows, current = sample()
        daily.append({"d": "2026-10-23", "c": daily[-1]["c"]*1.10, "v": 1000})
        current = current.replace(hour=20, minute=30)
        market = {"dates": [r["d"] for r in daily], "stocks": [{"c": "123456", "n": "Example",
                   "m": "KOSDAQ", "s": 0, "p": [r["c"] for r in daily], "v": [r["v"] for r in daily]}]}
        charts = {"daily": {"history": daily, "dataStatus": "ok", "marketStatus": "CLOSE"}, "intraday": {"history": rows, "source": "naver_native", "dataStatus": "ok"},
                  "quote": {"price": daily[-1]["c"], "time": current.isoformat()}}
        refresh = Mock(side_effect=lambda *args: copy.deepcopy(charts))
        return market, current, refresh

    def test_bootstrap_no_alert_and_duplicate_registration(self):
        market, current, refresh = self.setup_data()
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=refresh, sender=sender)
            self.assertTrue(p["candidates"][0]["initialImport"])
            original = p["candidates"][0]["registeredAt"]
            p = update(p, market, current+dt.timedelta(minutes=10), volumes={}, refresh_fn=refresh, sender=sender)
            self.assertEqual(len(p["candidates"]), 1)
            self.assertEqual(p["candidates"][0]["registeredAt"], original)
        sender.assert_not_called()

    def test_acknowledged_registration_only_and_retry(self):
        market, current, refresh = self.setup_data()
        sender = Mock(side_effect=OSError("offline"))
        p = {"initializedAt": "2026-10-22T20:00+09:00"}
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            p = update(p, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=refresh, sender=sender)
            self.assertTrue(p["candidates"][0]["registrationNoticePending"])
            self.assertFalse(p.get("notificationHistory"))
            sender.side_effect = None
            p = update(p, market, current+dt.timedelta(minutes=10), volumes={}, refresh_fn=refresh, sender=sender)
            self.assertFalse(p["candidates"][0]["registrationNoticePending"])
            self.assertEqual(len(p["notificationHistory"]), 1)
            self.assertIn("전일대비", sender.call_args.args[1])
            update(p, market, current+dt.timedelta(minutes=20), volumes={}, refresh_fn=refresh, sender=sender)
            self.assertEqual(sender.call_count, 2)

    def test_manual_exclusion_and_charts_only_do_not_register(self):
        market, current, refresh = self.setup_data()
        volumes = {"123456": {"day": "2026-10-23", "volume": 1000}}
        p = update({}, market, current, volumes=volumes, refresh_fn=refresh, excluded=["123456"])
        self.assertEqual(p["candidates"], [])
        p = update({}, market, current, volumes=volumes, refresh_fn=refresh, charts_only=True)
        self.assertEqual(p["candidates"], [])

    def test_native_only_common_image_alerts_and_dedup(self):
        payload, rules, board = fixture()
        candidate = payload["candidates"][0]
        candidate["candidateTier"] = "first_rise"
        candidate["tradingDaysRemaining"] = 10
        candidate["dailySignalDate"] = "2026-10-14"
        candidate["closeConfirmation"] = {"qualified": True, "ruleVersion": VERSION}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"first.json"
            path.write_text(json.dumps({"candidates": [candidate]}), encoding="utf8")
            self.assertEqual(load_first_rise_candidates(path, at("11:00"))[0]["candidateTier"], "first_rise")
        sender = Mock()
        process(payload, at("11:00"), "test", False, board, rules, sender)
        self.assertEqual(process(payload, at("11:30"), "test", False, board, rules, sender)[0], 1)
        self.assertEqual(process(payload, at("11:40"), "test", False, board, rules, sender)[0], 0)
        ag = {"code": candidate["code"], "candidateTier": "core"}
        self.assertEqual(merge_candidates([ag], [candidate]), [ag])

    def test_first_day_image_catchup_is_not_sent_after_close(self):
        payload, rules, board = fixture()
        candidate = payload["candidates"][0]
        candidate.update(candidateTier="first_rise", dailySignalDate="2026-10-15", registeredAt=at("20:30").isoformat(), initialImport=False)
        sender = Mock()
        self.assertEqual(process(payload, at("20:30"), "test", False, board, rules, sender)[0], 0)
        sender.assert_not_called()

    def test_intraday_never_scans_registers_or_sends_even_with_future_daily_data(self):
        market, current, refresh = self.setup_data()
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            p = update({"initializedAt": "old"}, market, current.replace(hour=13),
                       volumes={"123456": {"day": "2026-10-23", "volume": 1000000}}, refresh_fn=refresh, sender=sender)
        self.assertEqual(p["candidates"], [])
        self.assertEqual(p["candidateScan"]["checkedStocks"], 0)
        refresh.assert_not_called()
        sender.assert_not_called()

    def test_intraday_spike_and_high_quote_do_not_replace_closing_price(self):
        market, current, refresh = self.setup_data()
        def low_close(*args):
            charts = refresh(*args)
            daily = charts["daily"]["history"]
            daily[-1]["c"] = daily[-2]["c"]*1.04
            charts["quote"]["price"] = 150
            return charts
        p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=low_close)
        self.assertEqual(p["candidates"], [])

    def test_delayed_open_market_snapshot_after_2030_is_not_confirmed(self):
        market, current, refresh = self.setup_data()
        def open_market(*args):
            charts = refresh(*args)
            charts["daily"]["marketStatus"] = "OPEN"
            return charts
        p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=open_market)
        self.assertEqual(p["candidates"], [])
        self.assertTrue(p["errors"])

    def test_quote_reference_only_changes_display_gain_not_daily_cross(self):
        market, current, refresh = self.setup_data()
        def official_reference(*args):
            charts = refresh(*args)
            close = charts["daily"]["history"][-1]["c"]
            charts["quote"].update(dataStatus="ok", previousClose=close/1.049)
            return charts
        p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=official_reference)
        self.assertEqual(len(p["candidates"]), 1)
        self.assertAlmostEqual(p["candidates"][0]["closeConfirmation"]["changePct"], 4.9)

    def test_old_candidates_revalidated_and_failures_archived_not_erased(self):
        market, current, refresh = self.setup_data()
        p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=refresh)
        item = p["candidates"][0]
        registered = item["registeredAt"]
        price = item["registrationPrice"]
        del item["closeConfirmation"]
        p = update(p, market, current, refresh_fn=refresh, charts_only=True)
        self.assertEqual(p["candidates"][0]["registeredAt"], registered)
        self.assertEqual(p["candidates"][0]["registrationPrice"], price)
        del p["candidates"][0]["closeConfirmation"]
        def low_close(*args):
            charts = refresh(*args)
            daily = charts["daily"]["history"]
            daily[-1]["c"] = daily[-2]["c"]*1.04
            return charts
        p = update(p, market, current, refresh_fn=low_close, charts_only=True)
        self.assertEqual(p["candidates"], [])
        self.assertEqual(p["history"][-1]["archiveReason"], "first_day_daily_ma3_cross10_not_met")
        self.assertEqual(p["history"][-1]["registeredAt"], registered)

    def test_unverified_legacy_candidate_is_quarantined_on_data_error(self):
        market, current, refresh = self.setup_data()
        old = {"code": "123456", "candidateTier": "first_rise", "dailySignalDate": "2026-10-23", "registeredAt": "old"}
        p = update({"initializedAt": "old", "candidates": [old]}, market, current,
                   refresh_fn=Mock(side_effect=OSError("offline")), charts_only=True)
        self.assertEqual(p["candidates"], [])
        self.assertEqual(p["pendingCloseValidation"], [old])

    def test_previous_five_percent_exclusion_is_rechecked_once_without_alert(self):
        market, current, refresh = self.setup_data()
        p = update({}, market, current, volumes={"123456": {"day": "2026-10-23", "volume": 1000}}, refresh_fn=refresh)
        old = p["candidates"].pop()
        old["closeConfirmation"]["ruleVersion"] = "first-rise-close5-v2"
        old.update(archiveReason="first_day_close_below_5pct", archivedAt=current.isoformat())
        p["history"].append(old)
        sender = Mock()
        p = update(p, market, current, refresh_fn=refresh, charts_only=True, sender=sender)
        self.assertEqual(len(p["candidates"]), 1)
        self.assertEqual(p["candidates"][0]["registeredAt"], old["registeredAt"])
        self.assertEqual(p["history"][0]["reviewedUnderVersion"], VERSION)
        p = update(p, market, current, refresh_fn=refresh, charts_only=True, sender=sender)
        self.assertEqual(len(p["history"]), 1)
        sender.assert_not_called()

    def test_first_rise_can_be_manually_excluded_without_entering_ag(self):
        from manage_candidate_exclusions import manage
        market, current, refresh = self.setup_data()
        candidate = {"code": "123456", "name": "Example", "candidateTier": "first_rise", "dailySignalDate": "2026-10-23",
                     "registeredAt": current.isoformat(), "registrationPrice": 100}
        payload = {"candidates": [], "history": [], "analysisCandidates": [candidate]}
        registry = {}
        manage(payload, registry, "exclude", "123456", "other", current)
        self.assertEqual(payload["analysisCandidates"], [])
        manage(payload, registry, "restore", "123456", "", current, ["2026-10-23"])
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["analysisCandidates"][0]["candidateTier"], "first_rise")


if __name__ == "__main__":
    unittest.main()
