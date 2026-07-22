from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from utils.operational_readiness import (
    build_reconciliation_report,
    decrypt_backup,
    encrypt_and_replicate_backup,
    operational_status,
    reconcile_with_iran_panel,
    run_restore_drill,
    verify_database_copy,
    write_reconciliation_csv,
)


class OperationalReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "live.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE advert_offers (id INTEGER PRIMARY KEY, advert_rowid INTEGER, status TEXT, rate_toman INTEGER, proposed_euro_amount INTEGER);
                CREATE TABLE euro_adverts (
                    id INTEGER PRIMARY KEY, euro_amount INTEGER, status TEXT,
                    fee_override_eur REAL, operation TEXT
                );
                CREATE TABLE offer_deal_gates (offer_id INTEGER PRIMARY KEY, advert_rowid INTEGER, gate_status TEXT, buyer_toman_settled_at INTEGER, seller_toman_settled_at INTEGER, completed_at INTEGER, started_at INTEGER);
                CREATE TABLE deal_delivery_queue (status TEXT, created_at INTEGER);
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
                """
            )
            conn.execute("INSERT INTO euro_adverts VALUES (10, 100, 'بسته', NULL, 'فروش')")
            conn.execute("INSERT INTO advert_offers VALUES (7, 10, 'gate_closed', 200000, 100)")
            conn.execute("INSERT INTO offer_deal_gates VALUES (7, 10, 'closed', 1, 2, 3, 1)")

    def tearDown(self):
        self.temp.cleanup()

    def test_restore_drill_is_disposable_and_complete(self):
        result = run_restore_drill(self.db)
        self.assertTrue(result["ok"])
        self.assertTrue(result["drill_copy_removed"])
        self.assertEqual(result["row_counts"]["offer_deal_gates"], 1)

    def test_missing_required_table_fails_verification(self):
        broken = self.root / "broken.db"
        sqlite3.connect(broken).close()
        result = verify_database_copy(broken)
        self.assertFalse(result["ok"])
        self.assertIn("offer_deal_gates", result["missing_tables"])

    def test_status_detects_recent_backup_and_clean_queue(self):
        backup_dir = self.root / "backups"
        backup_dir.mkdir()
        backup = backup_dir / "live.db.1.bak"
        backup.write_bytes(self.db.read_bytes())
        result = operational_status(self.db, backup_dir, now=int(time.time()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery_queue"]["failed"], 0)

    def test_reconciliation_writes_excel_friendly_csv(self):
        rows = build_reconciliation_report(self.db)
        self.assertEqual(rows[0]["gross_toman"], 20_000_000)
        self.assertEqual(rows[0]["buyer_expected_toman"], 20_500_000)
        self.assertEqual(rows[0]["seller_expected_toman"], 19_500_000)
        self.assertEqual(rows[0]["issues"], [])
        target = write_reconciliation_csv(rows, self.root / "report.csv")
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_panel_reconciliation_requires_unique_directional_amount(self):
        rows = build_reconciliation_report(self.db)
        reconciled = reconcile_with_iran_panel(
            rows,
            [
                {"id": 11, "iran_type": "in", "iran_amount": 20_500_000},
                {"id": 12, "iran_type": "out", "iran_amount": 19_500_000},
            ],
        )
        self.assertEqual(reconciled[0]["panel_buyer_status"], "matched")
        self.assertEqual(reconciled[0]["panel_seller_status"], "matched")
        self.assertEqual(reconciled[0]["issues"], [])

    def test_panel_reconciliation_flags_ambiguous_match(self):
        rows = build_reconciliation_report(self.db)
        tx = {"iran_type": "in", "iran_amount": 20_500_000}
        reconciled = reconcile_with_iran_panel(rows, [{"id": 1, **tx}, {"id": 2, **tx}])
        self.assertEqual(reconciled[0]["panel_buyer_status"], "ambiguous")
        self.assertIn("panel_buyer_ambiguous", reconciled[0]["issues"])

    def test_panel_reconciliation_accepts_receipt_amount_in_rial(self):
        rows = build_reconciliation_report(self.db)
        reconciled = reconcile_with_iran_panel(
            rows,
            [
                {"id": 21, "iran_type": "ورودی", "iran_amount": 205_000_000},
                {"id": 22, "iran_type": "خروجی", "iran_amount": 195_000_000},
            ],
        )
        self.assertEqual(reconciled[0]["panel_buyer_status"], "matched")
        self.assertEqual(reconciled[0]["panel_buyer_unit"], "rial")
        self.assertEqual(reconciled[0]["panel_seller_status"], "matched")

    def test_encrypted_replica_round_trip(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography is not installed")
        key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        encrypted = encrypt_and_replicate_backup(self.db, self.root / "offsite", key)
        restored = decrypt_backup(encrypted, self.root / "restored.db", key)
        self.assertTrue(verify_database_copy(restored)["ok"])
        self.assertTrue(encrypted.with_suffix(encrypted.suffix + ".sha256").is_file())


if __name__ == "__main__":
    unittest.main()
