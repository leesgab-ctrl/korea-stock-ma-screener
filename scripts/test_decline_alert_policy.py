import copy
import json
import unittest
from unittest.mock import Mock

import image_pattern_search as search
import test_image_pattern_search as fixtures


class DeclineAlertPolicyTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.rules, self.board = fixtures.fixture()
        for i, row in enumerate(self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]):
            row["m120"] = 90 + i / 10
        for rule in self.rules:
            rule.update(notificationsEnabled=False, requireRisingMa120ForConfirmation=True,
                        requireMa60AboveMa120ForConfirmation=True)
        self.sender = Mock()

    def run_at(self, time):
        return search.process(self.payload, fixtures.at(time), "test", False, self.board, self.rules, self.sender)

    def test_production_mutes_both_decline_rules_but_not_ma120_or_other_active_alerts(self):
        rules = json.loads(search.RULES_PATH.read_text(encoding="utf8"))["rules"]
        muted = {"daesung-decline-ma40-recovery", "daehan-recovery-pattern"}
        for rule in rules:
            if rule["id"] in muted:
                self.assertTrue(rule["enabled"])
                self.assertFalse(rule["notificationsEnabled"])
            elif rule["enabled"]:
                self.assertIsNot(rule.get("notificationsEnabled"), False)

    def test_mute_blocks_primary_and_confirmation_but_retains_capture_display(self):
        for time in ("11:00", "11:30", "12:00", "12:10"):
            self.assertEqual(self.run_at(time)[0], 0)
        self.sender.assert_not_called()
        self.assertEqual(len(self.payload["imageSearch"]["events"]), 3)
        self.assertEqual(len(self.payload["imageSearch"]["matches"]), 2)
        self.assertTrue(all(rule["active"] and not rule["notificationsEnabled"] for rule in self.payload["imageSearch"]["rules"]))

    def test_ah_enrollment_cannot_bypass_mute(self):
        self.payload["candidates"][0].update(candidateTier="ah", registeredAt="2026-10-15T18:00+09:00")
        self.assertEqual(self.run_at("18:10")[0], 0)
        self.sender.assert_not_called()
        self.assertFalse(self.payload["ahImageEnrollmentState"])

    def test_mute_does_not_remove_past_receipts_or_sent_signatures(self):
        old = {"id": "old", "code": "000001", "label": "old notification"}
        self.payload["notificationHistory"] = [copy.deepcopy(old)]
        self.run_at("11:00")
        saved = next(iter(self.payload["imageSearchNotificationState"].values()))
        saved["sent"]["old-signature"] = fixtures.at("10:00").isoformat()
        self.run_at("12:00")
        self.assertEqual(self.payload["notificationHistory"], [old])
        self.assertIn("old-signature", saved["sent"])

    def test_muted_period_is_not_replayed_if_reenabled_later(self):
        self.run_at("11:00")
        self.run_at("12:00")
        for rule in self.rules:
            rule["notificationsEnabled"] = True
        self.assertEqual(self.run_at("12:10")[0], 0)
        self.sender.assert_not_called()

    def test_other_rule_notifications_continue(self):
        self.rules[1]["notificationsEnabled"] = True
        for time in ("11:00", "11:30", "12:00"):
            self.run_at(time)
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.sender.call_args.args[4]["ruleName"], "Daehan")


if __name__ == "__main__":
    unittest.main()
