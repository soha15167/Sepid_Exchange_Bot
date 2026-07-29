"""Quiet deal maintenance and operational metadata tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch


class DealOperationsTests(unittest.TestCase):
    def test_verified_backup_records_health_metadata(self):
        from utils import deal_operations

        destination = Path("verified.bak")
        with (
            patch.object(deal_operations, "resolve_database_path", return_value=Path("db")),
            patch.object(deal_operations, "configured_backup_retention", return_value=14),
            patch.object(deal_operations, "create_verified_backup", return_value=destination),
            patch.object(deal_operations, "set_setting") as setting,
        ):
            self.assertEqual(deal_operations.run_deal_verified_backup(), destination)

        values = {call.args[0]: call.args[1] for call in setting.call_args_list}
        self.assertEqual(values["deal_last_backup_path"], str(destination))
        self.assertEqual(values["deal_last_backup_error"], "")

    def test_privacy_job_is_silent_and_records_completion(self):
        from utils import deal_operations

        with (
            patch.object(deal_operations, "deal_privacy_redact_expired", return_value=3),
            patch.object(deal_operations, "set_setting") as setting,
        ):
            self.assertEqual(deal_operations.run_deal_privacy_retention(), 3)

        keys = {call.args[0] for call in setting.call_args_list}
        self.assertIn("deal_last_privacy_run_at", keys)
        self.assertIn("deal_last_privacy_redacted", keys)

    def test_restore_drill_records_success_without_notifications(self):
        from utils import deal_operations

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "db.1.bak"
            backup.write_bytes(b"placeholder")
            with (
                patch.object(deal_operations, "DEAL_BACKUP_DIR", tmp),
                patch.object(deal_operations, "run_restore_drill", return_value={"ok": True}),
                patch.object(deal_operations, "set_setting") as setting,
            ):
                result = deal_operations.run_deal_restore_drill()
        self.assertTrue(result["ok"])
        keys = {call.args[0] for call in setting.call_args_list}
        self.assertIn("deal_last_restore_drill_at", keys)
        self.assertIn("deal_last_restore_drill_error", keys)

    def test_reconciliation_writes_report_and_metadata(self):
        from utils import deal_operations

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "report.csv"
            with (
                patch.object(deal_operations, "DEAL_RECONCILIATION_DIR", tmp),
                patch.object(deal_operations, "resolve_database_path", return_value=Path("db")),
                patch.object(deal_operations, "build_reconciliation_report", return_value=[{"issues": ["x"]}]),
                patch.object(deal_operations, "get_transactions", return_value=(True, [])),
                patch.object(deal_operations, "write_reconciliation_csv", return_value=destination),
                patch.object(deal_operations, "set_setting") as setting,
            ):
                result = deal_operations.run_deal_reconciliation()
        self.assertEqual(result, destination)
        values = {call.args[0]: call.args[1] for call in setting.call_args_list}
        self.assertEqual(values["deal_last_reconciliation_issues"], "1")
        self.assertEqual(values["deal_last_reconciliation_error"], "")


if __name__ == "__main__":
    unittest.main()
