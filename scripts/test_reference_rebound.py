import datetime as dt
import unittest
from unittest import mock

import update_candidate_monitor as monitor


class ReferenceReboundTests(unittest.TestCase):
    def rows(self):
        rows = []
        for index in range(10):
            day = "2026-10-06" if index < 5 else "2026-10-07"
            slot = index if index < 5 else index - 5
            m20 = 100 + index * 0.01
            rows.append({"t": f"{day}T{9 + slot // 2:02}:{(slot % 2) * 30:02}+09:00", "c": 101,
                         "m10": m20 + (0.1 if index not in (6, 7) else -0.1),
                         "m3": m20 + (0.2 if index not in (6, 7) else -0.2),
                         "m20": m20, "m40": 99.7 + index * 0.01,
                         "m60": 102 + index * 0.01, "complete": True})
        return rows

    def test_yellow_stops_on_rebound_and_ma60_position_unrestricted(self):
        rows = self.rows()
        events = monitor.apply_reference_rebound(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], rows[8]["t"])
        self.assertTrue(rows[6]["referencePullback"])
        self.assertTrue(rows[7]["referencePullback"])
        self.assertFalse(rows[8]["referencePullback"])
        self.assertTrue(rows[8]["referenceRebound"])

    def test_structure_break_cancels(self):
        for key in ("m40", "m60"):
            rows = self.rows()
            rows[7][key] = rows[6][key] - 1
            self.assertEqual(monitor.apply_reference_rebound(rows), [])
        rows = self.rows()
        rows[7]["m20"] = rows[7]["m40"] - 1
        self.assertEqual(monitor.apply_reference_rebound(rows), [])

    def test_ma3_can_signal_before_ma10_recovers(self):
        rows = self.rows()
        rows[7]["m3"] = rows[7]["m20"] + 0.2
        events = monitor.apply_reference_rebound(rows)
        self.assertEqual(events[0]["time"], rows[7]["t"])
        self.assertLess(rows[7]["m10"], rows[7]["m20"])
        self.assertFalse(rows[7]["referencePullback"])

    def test_ma10_recovery_alone_does_not_signal(self):
        rows = self.rows()
        for row in rows[6:]:
            row["m3"] = row["m20"] - 0.2
        self.assertEqual(monitor.apply_reference_rebound(rows), [])
        self.assertTrue(rows[-1]["referencePullback"])

    def test_gap_flatness_yesterday_and_incomplete(self):
        for change in ("gap", "flat", "yesterday", "incomplete"):
            rows = self.rows()[:9]
            if change == "gap":
                for row in rows:
                    row["m40"] -= 3
            elif change == "flat":
                rows[6]["m20"] += 1
            elif change == "yesterday":
                rows[0]["m20"] = 103
            else:
                rows[8]["complete"] = False
            self.assertEqual(monitor.apply_reference_rebound(rows), [])

    def test_notification_dedup_no_notify_and_registration(self):
        rows = self.rows()
        events = monitor.apply_reference_rebound(rows)
        current = dt.datetime.fromisoformat(events[0]["time"]) + dt.timedelta(minutes=32)
        candidate = {"code": "000001", "name": "Test", "dailySignalDate": "2026-10-06", "registeredAt": "2026-10-06",
                     "displayCharts": {"intraday": {"dataStatus": "ok", "referenceRebounds": events}}}
        payload = {"candidates": [candidate]}
        with mock.patch.dict(monitor.os.environ, {"NTFY_TOPIC": "test"}), mock.patch.object(monitor.urllib.request, "urlopen") as send:
            send.return_value.__enter__.return_value.status = 200
            self.assertEqual(monitor.notify_reference_rebounds(payload, current, True), (0, 1))
            send.assert_not_called()
            self.assertEqual(monitor.notify_reference_rebounds(payload, current), (1, 1))
            self.assertEqual(monitor.notify_reference_rebounds(payload, current), (0, 0))
            self.assertEqual(send.call_count, 1)
            payload["notifiedSignals"] = []
            candidate["registeredAt"] = "2026-10-08"
            self.assertEqual(monitor.notify_reference_rebounds(payload, current), (0, 0))


if __name__ == "__main__":
    unittest.main()
