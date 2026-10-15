import copy
import datetime as dt
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import update_candidate_monitor as monitor
from manage_candidate_exclusions import manage


def candidate(code, tier):
    return {"code": code, "name": code, "candidateTier": tier, "status": "watching",
            "dailySignalDate": "2026-10-12", "registeredAt": "2026-10-12T17:00:00+09:00",
            "registrationPrice": 100, "daily": {"close": 100},
            "displayCharts": {"daily": {"series": [{"d": "2026-10-13", "c": 101}]}}}


class ExpandedAgRetirementTests(unittest.TestCase):
    def test_only_expanded_ag_is_retired_and_records_are_preserved(self):
        core = candidate("000001", monitor.CORE_TIER)
        expanded = candidate("000002", monitor.EXPANDED_TIER)
        ak = candidate("000003", monitor.AK_TIER)
        payload = {"candidates": [core, expanded, ak], "history": [],
                   "paperTrading": {"positions": [{"code": "000002", "status": "open"}]},
                   "holdingCharts": {"000002": {"saved": True}},
                   "colorNotificationState": {"saved": True},
                   "coreMorningNotificationState": {"saved": True}}
        before = copy.deepcopy(payload)
        self.assertEqual(monitor.retire_expanded_ag(payload, "2026-10-15"), 1)
        self.assertEqual(payload["candidates"], [core, ak])
        record = payload["history"][0]
        self.assertEqual(record["archiveReason"], "expanded_ag_disabled")
        for key in ("registeredAt", "registrationPrice", "candidateTier", "displayCharts"):
            self.assertEqual(record[key], expanded[key])
        for key in ("paperTrading", "holdingCharts", "colorNotificationState", "coreMorningNotificationState"):
            self.assertEqual(payload[key], before[key])
        self.assertEqual(payload["summary"]["active"], 2)
        self.assertEqual(payload["summary"]["expandedCandidates"], 0)
        self.assertEqual(monitor.retire_expanded_ag(payload, "2026-10-15"), 0)
        self.assertEqual(len(payload["history"]), 1)

    def test_old_expanded_history_is_not_automatically_restored(self):
        records = []
        for index, tier in enumerate((monitor.CORE_TIER, monitor.EXPANDED_TIER, monitor.AK_TIER), 1):
            item = candidate(str(index).zfill(6), tier)
            item.update(id=str(index), archiveReason="window_completed")
            records.append(item)
        payload = {"history": records, "candidates": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(monitor, "ROOT", Path(folder)):
            monitor.restore_ten_day_candidates(payload, ["2026-10-12", "2026-10-13"], "2026-10-13")
        self.assertEqual([item["candidateTier"] for item in payload["candidates"]], [monitor.CORE_TIER, monitor.AK_TIER])
        self.assertEqual(payload["history"][0]["candidateTier"], monitor.EXPANDED_TIER)

    def test_manual_restore_cannot_reactivate_expanded_ag(self):
        item = candidate("000002", monitor.EXPANDED_TIER)
        record = monitor.archive_candidate(item, "2026-10-13", "manual_excluded")
        payload = {"candidates": [], "history": [record]}
        registry = {"excluded": [{"code": item["code"], "candidate": item}]}
        manage(payload, registry, "restore", item["code"], "", dt.datetime(2026, 10, 15, tzinfo=monitor.KST),
               ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"])
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["history"], [record])

    def test_charts_mode_removes_expanded_before_fetching_or_alerting(self):
        payload = {"candidates": [candidate("000001", monitor.CORE_TIER), candidate("000002", monitor.EXPANDED_TIER)]}
        current = dt.datetime(2026, 10, 15, 20, tzinfo=monitor.KST)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(mode="charts", output="data/candidate-monitor.json", stock_data="data/stock-data.json",
                             minute_count=5000, no_notify=True, now=current.isoformat(), github_output=None)
            with patch.object(monitor, "ROOT", root), patch.object(monitor, "parse_args", return_value=args), \
                 patch.object(monitor, "load_previous", return_value=payload), \
                 patch.object(monitor, "refresh_display_charts") as refresh, \
                 patch.object(monitor, "refresh_excluded_verification", return_value=False), \
                 patch.object(monitor, "refresh_reference_verification", return_value=False), \
                 patch("color_notifications.initialize_recovery_baselines"), \
                 patch("color_notifications.notify") as alerts:
                monitor.main()
            self.assertEqual([item["code"] for item in refresh.call_args.args[0]["candidates"]], ["000001"])
            alerts.assert_not_called()
            saved = json.loads((root / args.output).read_text(encoding="utf-8"))
            self.assertEqual(saved["history"][0]["archiveReason"], "expanded_ag_disabled")


if __name__ == "__main__":
    unittest.main()
