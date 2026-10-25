import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from first_rise_rules import VERSION, confirmed, preliminary_confirmation, preliminary_window
from update_first_rise_monitor import update
import test_first_rise as first_rise_tests
from common_monitor import load_first_rise_candidates


class PreliminaryFirstRiseTests(unittest.TestCase):
    def data(self):
        market, final_time, base_refresh = first_rise_tests.FirstRiseMonitorTests().setup_data()
        current = final_time.replace(hour=15, minute=10)
        def refresh(*args):
            charts = base_refresh(*args)
            charts["daily"]["marketStatus"] = "OPEN"
            charts["quote"].update(dataStatus="ok", time=current.isoformat(),
                                   previousClose=charts["daily"]["history"][-2]["c"])
            return charts
        volumes = {"123456": {"day": current.date().isoformat(), "volume": 1000}}
        return market, current, final_time, refresh, base_refresh, volumes

    def test_window_boundaries_and_stale_or_future_quotes(self):
        market, current, _, refresh, _, _ = self.data()
        self.assertFalse(preliminary_window(current.replace(minute=9, second=59)))
        self.assertTrue(preliminary_window(current.replace(minute=15, second=59)))
        self.assertFalse(preliminary_window(current.replace(minute=16)))
        charts = refresh()
        self.assertTrue(preliminary_confirmation(charts["daily"]["history"], '2026-10-23', current, charts["quote"])["qualified"])
        for stamp in ['2026-10-23T14:00:00+09:00', '2026-10-23T20:30:00+09:00', '2026-10-23T15:10:00']:
            charts["quote"]["time"] = stamp
            self.assertIsNone(preliminary_confirmation(charts["daily"]["history"], '2026-10-23', current, charts["quote"]))

    def test_future_final_close_is_not_used_for_preliminary_mas(self):
        _, current, _, refresh, _, _ = self.data()
        charts = refresh()
        original = preliminary_confirmation(charts["daily"]["history"], '2026-10-23', current, charts["quote"])
        charts["daily"]["history"][-1]["c"] = 9999999
        actual = preliminary_confirmation(charts["daily"]["history"], '2026-10-23', current, charts["quote"])
        self.assertEqual(actual, original)
        self.assertFalse(confirmed({"closeConfirmation": actual}))

    def test_preliminary_is_separate_and_notified_once_without_delivery_markers(self):
        market, current, _, refresh, _, volumes = self.data()
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            payload = update({"initializedAt": "old"}, market, current, volumes=volumes, refresh_fn=refresh, sender=sender)
            self.assertEqual(payload["candidates"], [])
            self.assertEqual(len(payload["preliminary"]["candidates"]), 1)
            self.assertFalse(payload.get("notificationHistory"))
            self.assertIn("최종 미확정", sender.call_args.args[1])
            self.assertIn("매수 신호가 아니며", sender.call_args.args[1])
            payload = update(payload, market, current.replace(minute=15), volumes=volumes, refresh_fn=refresh, sender=sender)
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(payload['preliminary']['generatedAt'], current.isoformat(timespec='seconds'))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'first.json'
                path.write_text(json.dumps(payload), encoding='utf8')
                self.assertEqual(load_first_rise_candidates(path, current), [])

    def test_no_late_preliminary_scan_and_charts_only_does_not_notify(self):
        market, current, _, refresh, _, volumes = self.data()
        for time, charts_only in [(current.replace(minute=16), False), (current, True)]:
            sender = Mock()
            payload = update({}, market, time, volumes=volumes, refresh_fn=refresh, sender=sender, charts_only=charts_only)
            self.assertNotIn('preliminary', payload)
            sender.assert_not_called()

    def test_final_confirmation_and_rejection_do_not_promote_preliminary_blindly(self):
        market, current, final_time, refresh, final_refresh, volumes = self.data()
        p = update({"initializedAt": "old"}, market, current, volumes=volumes, refresh_fn=refresh, no_notify=True)
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            accepted = update(copy.deepcopy(p), market, final_time, volumes=volumes, refresh_fn=final_refresh, sender=sender)
            self.assertEqual(len(accepted["candidates"]), 1)
            self.assertEqual(accepted["preliminary"]["candidates"][0]["finalVerdict"], "confirmed")
            self.assertTrue(accepted["preliminary"]["finalNotifiedAt"])
            def fallen(*args):
                charts = final_refresh(*args)
                charts["daily"]["history"][-1]["c"] = 100
                charts["quote"]["price"] = 100
                return charts
            rejected = update(copy.deepcopy(p), market, final_time, volumes=volumes, refresh_fn=fallen, sender=sender)
            self.assertEqual(rejected['candidates'], [])
            self.assertEqual(rejected['preliminary']['candidates'][0]['finalVerdict'], 'not_met')
            self.assertIn('최종 미충족: Example', sender.call_args.args[1])
            count = sender.call_count
            update(rejected, market, final_time.replace(minute=40), volumes=volumes, refresh_fn=fallen, sender=sender)
            self.assertEqual(sender.call_count, count)

    def test_data_errors_are_pending_not_false_final_rejections(self):
        market, current, final_time, refresh, _, volumes = self.data()
        p = update({}, market, current, volumes=volumes, refresh_fn=refresh, no_notify=True)
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            p = update(p, market, final_time, volumes=volumes, refresh_fn=Mock(side_effect=OSError('offline')), sender=sender)
        self.assertEqual(p['preliminary']['candidates'][0]['finalVerdict'], 'pending')
        self.assertNotIn('finalizedAt', p['preliminary'])
        sender.assert_not_called()

    def test_preliminary_notification_failure_retries_only_inside_window(self):
        market, current, _, refresh, _, volumes = self.data()
        sender = Mock(side_effect=OSError('offline'))
        with patch.dict(os.environ, NTFY_TOPIC="test"):
            p = update({}, market, current, volumes=volumes, refresh_fn=refresh, sender=sender)
            self.assertFalse(p['preliminary']['notifiedAt'])
            sender.side_effect = None
            p = update(p, market, current.replace(minute=15), volumes=volumes, refresh_fn=refresh, sender=sender)
            self.assertTrue(p['preliminary']['notifiedAt'])
            update(p, market, current.replace(minute=20), volumes=volumes, refresh_fn=refresh, sender=sender)
            self.assertEqual(sender.call_count, 2)

    def test_empty_first_stage_is_finalized_and_final_notification_can_retry(self):
        market, _, final_time, _, final_refresh, volumes = self.data()
        sender = Mock()
        with patch.dict(os.environ, NTFY_TOPIC='test'):
            p = update({'initializedAt': 'old', 'preliminary': {'day': '2026-10-23', 'candidates': []}}, market,
                       final_time, volumes={}, refresh_fn=final_refresh, sender=sender)
            self.assertTrue(p['preliminary']['finalizedAt'])
            sender.assert_not_called()
        _, current, _, refresh, _, _ = self.data()
        p = update({}, market, current, volumes=volumes, refresh_fn=refresh, no_notify=True)
        sender = Mock(side_effect=OSError('offline'))
        with patch.dict(os.environ, NTFY_TOPIC='test'):
            p = update(p, market, final_time, volumes=volumes, refresh_fn=final_refresh, sender=sender)
            self.assertTrue(p['preliminary']['finalizedAt'])
            self.assertNotIn('finalNotifiedAt', p['preliminary'])
            sender.side_effect = None
            p = update(p, market, final_time.replace(minute=40), volumes=volumes, refresh_fn=final_refresh, sender=sender)
            self.assertTrue(p['preliminary']['finalNotifiedAt'])
            self.assertEqual(p['preliminary']['candidates'][0]['finalVerdict'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
