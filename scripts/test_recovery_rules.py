import copy
import datetime as dt
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import color_notifications as colors
from recovery_rules import recovery_states


def bars():
    values = [(103, 104, 102), (96, 98, 97), (99, 98, 99), (101, 99, 100),
              (110, 101, 99), (111, 102, 101), (112, 103, 99), (113, 104, 102)]
    start = dt.datetime.fromisoformat("2026-10-15T09:00:00+09:00")
    return [{"t": (start + dt.timedelta(minutes=30 * index)).isoformat(), "m3": m3,
             "m20": m20, "m10": m10, "m40": 100, "m60": 110, "complete": True, "c": 100}
            for index, (m3, m20, m10) in enumerate(values)]


class RecoveryRulesTest(unittest.TestCase):
    def test_interval_and_one_ma10_cross_per_cycle(self):
        result = recovery_states(bars())
        self.assertEqual([r["phase"] for r in result], ["before", "pullback", "fast", "fast", "fast", "fast", "confirmed", "confirmed"])
        self.assertTrue(result[5]["recoveryReached60"])
        self.assertFalse(result[4]["recoveryReached60"], "Touching MA60 is not a breakout")
        self.assertEqual([i for i, r in enumerate(result) if r["recoveryMa10Breakout"]], [5])
        self.assertTrue(result[5]["recoveryMa10Breakout"], "MA3 above MA60 must not suppress the alert")

    def test_incomplete_and_missing_values_do_not_trigger(self):
        rows = bars()[:6]
        rows[-1]["complete"] = False
        state = recovery_states(rows)[-1]
        self.assertTrue(state["recoveryActive"])
        self.assertFalse(state["recoveryReached60"])
        self.assertFalse(state["recoveryMa10Breakout"])
        rows[-1]["complete"] = True
        rows[-2]["m10"] = None
        self.assertFalse(recovery_states(rows)[-1]["recoveryMa10Breakout"])

    def test_no_pullback_means_no_recovery_alert(self):
        rows = bars()[2:]
        self.assertTrue(all(not r["recoveryMa10Breakout"] for r in recovery_states(rows)))

    def test_new_pullback_rearms(self):
        rows = bars()
        rows += [{**rows[-1], "t": "2026-10-15T13:00:00+09:00", "m20": 99, "m3": 98, "m10": 99},
                 {**rows[-1], "t": "2026-10-15T13:30:00+09:00", "m20": 99, "m3": 101, "m10": 101}]
        self.assertTrue(recovery_states(rows)[-1]["recoveryMa10Breakout"])

    def payload(self, rows):
        return {"candidates": [{"code": "123456", "name": "회복테스트", "registeredAt": "2026-10-14T16:00:00+09:00",
                                "displayCharts": {"intraday": {"series": rows}, "daily": {"history": []}}}]}

    def notify(self, payload, now="2026-10-15T12:01:00+09:00", sender=None):
        return colors.notify(payload, dt.datetime.fromisoformat(now), "test", sender=sender or Mock())

    def test_baseline_then_live_alert_retry_and_no_replay(self):
        payload = self.payload(bars()[:5])
        sender = Mock()
        self.assertEqual(self.notify(payload, sender=sender), (0, True))
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()[:6]
        sender.side_effect = RuntimeError("temporary send error")
        self.assertEqual(self.notify(payload, sender=sender)[0], 0)
        sender.side_effect = None
        self.assertEqual(self.notify(payload, sender=sender)[0], 1)
        self.assertEqual(sender.call_args.args[5], "recoveryMa10Breakout")
        self.assertEqual(self.notify(payload, sender=sender), (0, False))
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()
        self.assertEqual(self.notify(payload, "2026-10-15T13:01:00+09:00", sender=sender)[0], 0)

    def test_migrated_state_never_replays_new_alert(self):
        payload = self.payload(bars()[:6])
        key = "123456|2026-10-14T16:00:00+09:00"
        payload["colorNotificationState"] = {key: {"last": bars()[4]["t"], "compact": False, "purple": False}}
        sender = Mock()
        self.notify(payload, sender=sender)
        sender.assert_not_called()
        self.assertEqual(payload["colorNotificationState"][key]["recoveryRuleVersion"], 1)

    def test_deployment_seed_preserves_existing_cursor_and_next_cross(self):
        payload = self.payload(bars()[:5])
        key = "123456|2026-10-14T16:00:00+09:00"
        cursor = bars()[3]["t"]
        payload["colorNotificationState"] = {key: {"last": cursor, "compact": False, "purple": False}}
        self.assertTrue(colors.initialize_recovery_baselines(payload))
        self.assertEqual(payload["colorNotificationState"][key]["last"], cursor)
        self.assertEqual(payload["colorNotificationState"][key]["recoveryBaseline"], bars()[4]["t"])
        self.assertFalse(colors.initialize_recovery_baselines(payload))
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()[:6]
        sender = Mock()
        self.assertEqual(self.notify(payload, sender=sender)[0], 1)
        self.assertEqual(sender.call_args.args[5], "recoveryMa10Breakout")

    def test_old_event_and_no_notify_do_not_send(self):
        payload = self.payload(bars()[:5])
        self.notify(payload)
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()[:6]
        sender = Mock()
        self.assertEqual(self.notify(payload, "2026-10-15T16:00:00+09:00", sender=sender)[0], 0)
        sender.assert_not_called()
        fresh = self.payload(bars())
        original = copy.deepcopy(fresh)
        self.assertEqual(colors.notify(fresh, dt.datetime.now(dt.timezone.utc), "test", no_notify=True, sender=sender), (0, False))
        self.assertEqual(fresh, original)

    def test_recovery_message_without_real_network(self):
        payload = self.payload(bars())
        response = Mock()
        response.read.return_value = b'{"attachment":{"url":"test"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(colors, "chart_image", return_value=b"image"), patch.object(colors.urllib.request, "urlopen", return_value=response) as opened:
            colors.send("test", payload["candidates"][0], payload, bars()[5], colors.states(bars())[5], "recoveryMa10Breakout",
                        dt.datetime.fromisoformat("2026-10-15T12:01:00+09:00"))
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(opened.call_args.args[0].full_url).query)["message"][0]
        self.assertIn("회복테스트의 MA10이 MA40을 상향 돌파했습니다.", message)
        self.assertIn("MA3이 MA60 아래에 있는지 확인", message)
        self.assertIn("MA10 101.00 / MA40 100.00", message)


if __name__ == "__main__":
    unittest.main()
