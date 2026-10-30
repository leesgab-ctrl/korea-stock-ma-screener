import copy
from email.header import decode_header, make_header
import io
import json
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import color_notifications as colors
import image_pattern_search as search
import test_image_pattern_search as fixtures


class DeclineMa10Ma120GateTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.rules, self.board = fixtures.fixture()
        self.rows = self.payload["candidates"][0]["displayCharts"]["intraday"]["series"]
        for i, row in enumerate(self.rows):
            row["m120"] = 90 + i / 10
        for rule in self.rules:
            rule.update(version=2, requireRisingMa120ForConfirmation=True)

    def test_both_production_connections_require_the_new_condition(self):
        rules = json.loads(search.RULES_PATH.read_text(encoding="utf8"))["rules"]
        for rule_id in ("daesung-decline-ma40-recovery", "daehan-recovery-pattern"):
            rule = next(rule for rule in rules if rule["id"] == rule_id)
            self.assertTrue(rule["requireRisingMa120ForConfirmation"])
            self.assertGreaterEqual(rule["version"], 2)

    def test_rising_ma120_allows_both_confirmation_paths(self):
        for rule in self.rules:
            events = search.detect(self.rows, rule)
            event = next(event for event in events if event["stage"] == "confirmation")
            self.assertGreater(event["m120"], event["m120Previous"])
            self.assertTrue(event["requireRisingMa120ForConfirmation"])

    def test_flat_falling_missing_and_invalid_ma120_block_only_confirmation(self):
        for value in (self.rows[-2]["m120"], 80, None, 0, True, float("nan"), float("inf")):
            rows = copy.deepcopy(self.rows)
            rows[-1]["m120"] = value
            self.assertEqual([event["stage"] for event in search.detect(rows, self.rules[0])], ["primary"])
            self.assertEqual(search.detect(rows, self.rules[1]), [])
        rows = copy.deepcopy(self.rows)
        rows[-2].pop("m120")
        self.assertEqual([event["stage"] for event in search.detect(rows, self.rules[0])], ["primary"])

    def test_later_ma120_rise_without_new_cross_does_not_delay_the_failed_confirmation(self):
        self.rows[-1]["m120"] = self.rows[-2]["m120"]
        self.rows.append({**self.rows[-1], "t": "2026-10-15T12:00+09:00", "m120": 92,
                          "m10": 107, "m40": 104, "m3": 108})
        self.assertEqual([event["stage"] for event in search.detect(self.rows, self.rules[0])], ["primary"])
        self.assertEqual(search.detect(self.rows, self.rules[1]), [])

    def test_pipeline_still_sends_primary_but_blocks_flat_confirmation(self):
        sender = Mock()
        self.rows[-1]["m120"] = self.rows[-2]["m120"]
        for when in ("11:00", "11:30", "12:00", "12:10"):
            search.process(self.payload, fixtures.at(when), "test", False, self.board, self.rules, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(sender.call_args.args[4]["stage"], "primary")

    def test_rising_confirmation_is_deduplicated_across_images(self):
        sender = Mock()
        for when in ("11:00", "11:30", "12:00", "12:10"):
            search.process(self.payload, fixtures.at(when), "test", False, self.board, self.rules, sender)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(sender.call_args.args[4]["stage"], "confirmation")
        self.assertIn("Daesung / Daehan", sender.call_args.args[4]["ruleName"])

    def test_ah_enrollment_does_not_replay_ineligible_ma10_confirmation(self):
        candidate = self.payload["candidates"][0]
        candidate.update(candidateTier="ah", registeredAt="2026-10-15T18:00+09:00")
        self.rows[-1]["m120"] = self.rows[-2]["m120"]
        sender = Mock()
        search.process(self.payload, fixtures.at("18:10"), "test", False, self.board, self.rules, sender)
        signals = sender.call_args.args[4]["enrollmentSignals"]
        self.assertTrue(signals)
        self.assertTrue(all(signal["stage"] == "primary" for signal in signals))

    def test_new_version_does_not_replay_or_rewrite_old_receipts(self):
        old = {"id": "old-delivery", "code": "000001", "label": "old condition"}
        self.payload["notificationHistory"] = [copy.deepcopy(old)]
        sender = Mock()
        search.process(self.payload, fixtures.at("12:10"), "test", False, self.board, self.rules, sender)
        sender.assert_not_called()
        self.assertEqual(self.payload["notificationHistory"], [old])

    def test_exact_title_and_ma120_values_in_confirmation_message(self):
        event = next(event for event in search.detect(self.rows, self.rules[0]) if event["stage"] == "confirmation")
        event["ruleName"] = "test condition"
        candidate = self.payload["candidates"][0]
        with patch.object(colors, "chart_image", return_value=b"image"), \
                patch.object(colors.delivery, "capture", return_value={"status": "error", "price": None}), \
                patch.object(colors.urllib.request, "urlopen", return_value=io.BytesIO(b'{"attachment":{"name":"ok"}}')) as opened:
            search.send_alert("test", candidate, self.payload, self.rows[-1], event, fixtures.at("12:00"))
        request = opened.call_args.args[0]
        title = str(make_header(decode_header(request.get_header("Title"))))
        self.assertEqual(title, "하락중인 MA10이 MA40 돌파 MA120은 상승중")
        message = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)["message"][0]
        self.assertIn(title, message)
        self.assertIn("90.40 → 90.50", message)
        self.assertIn(candidate["name"], message)
        condition = self.payload["notificationHistory"][-1]["condition"]
        self.assertTrue(condition["requireRisingMa120ForConfirmation"])
        self.assertEqual(condition["ruleVersion"], 2)


if __name__ == "__main__":
    unittest.main()
