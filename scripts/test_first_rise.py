import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from first_rise_rules import KST, daily_context, detect, remaining_days
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

    def test_reference_examples_with_offline_native_data(self):
        root = Path(__file__).resolve().parents[1]/"outputs"
        for code, day, time in [("138360", "2026-10-20", "14:00"), ("300120", "2026-10-21", "13:30")]:
            minute = root/f"current-long-ma-pullback/charts/{code}.json"
            daily_path = root/f"daily-long-ma-pullback/charts/{code}.json"
            if not minute.exists() or not daily_path.exists():
                self.skipTest("Offline research caches are not deployed")
            rows = json.loads(minute.read_text(encoding="utf8"))["history"]
            daily = json.loads(daily_path.read_text(encoding="utf8"))["history"]
            events = detect(rows, daily, day, dt.datetime.fromisoformat(day+"T20:00+09:00"))
            self.assertEqual(events[0]["completedAt"][11:16], time)
            self.assertGreaterEqual(events[0]["dailyVolumeMultiple"], 3)


class FirstRiseMonitorTests(unittest.TestCase):
    def setup_data(self):
        daily, rows, current = sample()
        market = {"dates": [r["d"] for r in daily]+["2026-10-23"], "stocks": [{"c": "123456", "n": "Example",
                   "m": "KOSDAQ", "s": 0, "p": [r["c"] for r in daily]+[110], "v": [r["v"] for r in daily]+[1000]}]}
        charts = {"daily": {"history": daily}, "intraday": {"history": rows, "source": "naver_native", "dataStatus": "ok"},
                  "quote": {"price": 110, "time": current.isoformat()}}
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

    def test_same_day_enrollment_catchup_is_once_and_initial_import_is_silent(self):
        payload, rules, board = fixture()
        candidate = payload["candidates"][0]
        candidate.update(candidateTier="first_rise", registeredAt=at("18:00").isoformat(), initialImport=False)
        sender = Mock()
        self.assertEqual(process(payload, at("18:00"), "test", False, board, rules, sender)[0], 1)
        self.assertTrue(sender.call_args.args[4]["enrollmentSignals"])
        self.assertEqual(process(payload, at("18:10"), "test", False, board, rules, sender)[0], 0)
        payload, rules, board = fixture()
        payload["candidates"][0].update(candidateTier="first_rise", registeredAt=at("18:00").isoformat(), initialImport=True)
        self.assertEqual(process(payload, at("18:00"), "test", False, board, rules, sender)[0], 0)

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
