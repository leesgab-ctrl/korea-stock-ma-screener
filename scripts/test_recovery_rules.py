import copy
import datetime as dt
import json
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

import color_notifications as colors
from recovery_rules import recovery_states


def bars():
    values = [(103, 104, 102), (96, 98, 97), (99, 98, 98), (101, 99, 100),
              (110, 101, 99), (111, 102, 101), (112, 103, 99), (113, 104, 102)]
    start = dt.datetime.fromisoformat("2026-10-15T09:00:00+09:00")
    return [{"t": (start + dt.timedelta(minutes=30 * index)).isoformat(), "m3": m3,
             "m20": m20, "m10": m10, "m40": 100, "m60": 110, "complete": True, "c": 100}
            for index, (m3, m20, m10) in enumerate(values)]


class RecoveryRulesTest(unittest.TestCase):
    def setUp(self):
        # Preserve historical transport regressions; production muting is tested separately.
        policy = patch.object(colors, "DISABLED_NOTIFICATION_KINDS", frozenset())
        policy.start()
        self.addCleanup(policy.stop)

    def test_sizitronics_returns_to_yellow_after_ma3_loses_ma10(self):
        rows = json.loads((Path(__file__).parent / "fixtures/sizitronics-recovery.json").read_text())
        result = recovery_states(rows)
        self.assertTrue(result[3]["recoveryActive"])
        self.assertTrue(result[5]["recoveryFailed"])
        self.assertEqual(result[5]["phase"], "pullback")
        self.assertTrue(result[6]["recoveryFailed"])
        self.assertTrue(result[8]["recoveryActive"])
        self.assertFalse(result[8]["recoveryFailed"])
        self.assertTrue(result[9]["recoveryFailed"])

    def test_ma3_at_or_below_ma10_cancels_until_new_ma20_cross(self):
        rows = bars()[:3]
        touch = {**rows[-1], "t": "2026-10-15T10:30:00+09:00", "m3": 100, "m10": 100}
        result = recovery_states(rows + [touch])[-1]
        self.assertEqual(result["phase"], "pullback")
        self.assertTrue(result["recoveryFailed"])
        self.assertFalse(result["recoveryActive"])
        self.assertFalse(result["recoveryStrengthened"])
        self.assertFalse(recovery_states(rows + [{**touch, "complete": False}])[-1]["recoveryFailed"])
        rows += [touch, {**touch, "t": "2026-10-15T11:00:00+09:00", "m3": 97, "m10": 98},
                 {**touch, "t": "2026-10-15T11:30:00+09:00", "m3": 100, "m10": 99}]
        self.assertTrue(recovery_states(rows)[-1]["recoveryActive"])
        self.assertFalse(recovery_states(rows)[-1]["recoveryFailed"])

    def test_ma3_below_ma10_also_cancels_completed_recovery(self):
        rows = bars()[:7]
        rows += [{**rows[-1], "m3": 100, "m10": 100, "t": "2026-10-15T12:30:00+09:00"}]
        self.assertEqual(recovery_states(rows)[-1]["phase"], "pullback")
        self.assertTrue(recovery_states(rows)[-1]["recoveryFailed"])

    def test_daesung_second_recovery_without_new_ma20_ma40_cross(self):
        rows = json.loads((Path(__file__).parent / "fixtures/daesung-recovery.json").read_text())
        result = recovery_states(rows)
        self.assertEqual([r["phase"] for r in result],
                         ["before", "pullback", "fast", "fast", "confirmed", "pullback", "fast", "fast", "fast", "confirmed"])
        self.assertNotEqual(result[5]["recoveryCycle"], result[1]["recoveryCycle"])
        self.assertTrue(result[7]["recoveryMa10Breakout"])
        self.assertTrue(result[8]["recoveryReached60"])
        self.assertFalse(result[9]["recoveryActive"])

    def test_recovery_rearm_requires_ma20_below_ma40_and_completed_bar(self):
        rows = json.loads((Path(__file__).parent / "fixtures/daesung-recovery.json").read_text())[:5]
        touch = {**rows[-1], "m3": rows[-1]["m20"], "m10": 4600, "t": "2026-10-12T11:00:00+09:00"}
        self.assertEqual(recovery_states(rows + [{**touch, "complete": False}])[-1]["phase"], "confirmed")
        self.assertEqual(recovery_states(rows + [{**touch, "m20": 4800}])[-1]["phase"], "confirmed")
        self.assertEqual(recovery_states(rows + [touch])[-1]["phase"], "pullback")

    def test_second_ma3_pullback_rearms_notification_without_ma20_cross(self):
        rows = bars()
        for row in rows[2:]:
            row["m20"] = 99 if row is not rows[2] else 98
        rows[6]["m3"] = 98
        rows[7]["m3"] = 100
        result = recovery_states(rows)
        self.assertEqual([i for i, row in enumerate(result) if row["recoveryMa10Breakout"]], [5, 7])
        payload = self.payload(rows[:5])
        sender = Mock()
        self.notify(payload, sender=sender)
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = rows[:6]
        self.assertEqual(self.notify(payload, sender=sender)[0], 1)
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = rows
        self.assertEqual(self.notify(payload, "2026-10-15T13:01:00+09:00", sender=sender)[0], 1)
        self.assertEqual(self.notify(payload, "2026-10-15T13:01:00+09:00", sender=sender)[0], 0)

    def test_interval_and_one_ma10_cross_per_cycle(self):
        result = recovery_states(bars())
        self.assertEqual([r["phase"] for r in result], ["before", "pullback", "fast", "fast", "fast", "fast", "confirmed", "confirmed"])
        self.assertTrue(result[5]["recoveryReached60"])
        self.assertFalse(result[4]["recoveryReached60"], "Touching MA60 is not a breakout")
        self.assertEqual([i for i, r in enumerate(result) if r["recoveryMa10Breakout"]], [5])
        self.assertTrue(result[5]["recoveryMa10Breakout"], "MA3 above MA60 must not suppress the alert")
        self.assertTrue(all(not row["recoveryStrengthened"] for row in result[:5]))
        self.assertTrue(result[5]["recoveryStrengthened"])

    def test_incomplete_and_missing_values_do_not_trigger(self):
        rows = bars()[:6]
        rows[-1]["complete"] = False
        state = recovery_states(rows)[-1]
        self.assertTrue(state["recoveryActive"])
        self.assertFalse(state["recoveryReached60"])
        self.assertFalse(state["recoveryMa10Breakout"])
        self.assertFalse(state["recoveryStrengthened"])
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


class RecoveryNotificationDisabledPolicyTest(unittest.TestCase):
    def make_payload(self):
        return RecoveryRulesTest().payload(bars()[:5])

    def run_notify(self, payload, sender):
        return colors.notify(payload, dt.datetime.fromisoformat("2026-10-15T12:01:00+09:00"), "test", sender=sender)

    def test_only_adjustment_recovery_notifications_are_disabled(self):
        self.assertEqual(colors.DISABLED_NOTIFICATION_KINDS, {"recoveryMa10Breakout"})
        self.assertTrue(colors.states(bars())[5]["recoveryMa10Breakout"])

    def test_muted_event_advances_cursor_without_sending_or_deleting_receipts(self):
        payload, sender = self.make_payload(), Mock()
        payload["notificationHistory"] = [{"id": "old-recovery", "kind": "recoveryMa10Breakout"}]
        self.run_notify(payload, sender)
        payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()[:6]
        self.assertEqual(self.run_notify(payload, sender)[0], 0)
        sender.assert_not_called()
        saved = next(iter(payload["colorNotificationState"].values()))
        self.assertEqual(saved["last"], bars()[5]["t"])
        self.assertEqual(payload["notificationHistory"], [{"id": "old-recovery", "kind": "recoveryMa10Breakout"}])
        with patch.object(colors, "DISABLED_NOTIFICATION_KINDS", frozenset()):
            self.assertEqual(self.run_notify(payload, sender)[0], 0)
        sender.assert_not_called()

    def test_overlapping_other_notifications_are_not_suppressed(self):
        for kind in ("compact", "purple", "convergenceBreakout"):
            payload, sender = self.make_payload(), Mock()
            self.run_notify(payload, sender)
            payload["candidates"][0]["displayCharts"]["intraday"]["series"] = bars()[:6]
            flags = colors.states(bars()[:6])
            flags[-1][kind] = True
            with patch.object(colors, "states", return_value=flags):
                self.assertEqual(self.run_notify(payload, sender)[0], 1, kind)
            self.assertEqual(sender.call_args.args[5], kind)


if __name__ == "__main__":
    unittest.main()
