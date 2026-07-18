"""Integration tests for the financial deal-gate state stored in SQLite."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import db


class DealGateDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "deal-gate.db"
        self.original_path = db.DB_PATH
        db.DB_PATH = str(self.path)
        db.ensure_schema()

    def tearDown(self):
        db.DB_PATH = self.original_path
        self._tmp.cleanup()

    def _create_gate(self):
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="pending",
        )

    def test_gate_state_transition_and_receipt_lifecycle(self):
        self._create_gate()
        gate = db.deal_gate_get(101)
        self.assertEqual(gate["gate_status"], "pending")
        self.assertEqual(gate["buyer_telegram_id"], 10)
        self.assertEqual(gate["seller_telegram_id"], 20)

        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            buyer_response="yes",
            seller_response="yes",
        )
        active = db.deal_gate_active_for_user(10)
        self.assertIsNotNone(active)
        self.assertEqual(active["gate_status"], "accounts")

        buyer_items = db.deal_gate_append_buyer_receipt(
            101, entry_type="text", text="synthetic buyer receipt"
        )
        self.assertEqual(len(buyer_items), 1)
        self.assertEqual(buyer_items[0]["type"], "text")

        seller_items = db.deal_gate_append_seller_receipt(
            101, entry_type="photo", file_id="synthetic-file-id"
        )
        self.assertEqual(len(seller_items), 1)
        self.assertEqual(seller_items[0]["buyer_confirmed_at"], 0)
        self.assertFalse(db.deal_gate_confirm_seller_receipt_buyer(101, 99))
        self.assertTrue(db.deal_gate_confirm_seller_receipt_buyer(101, 0))
        confirmed = db.deal_gate_seller_receipt_list(101)[0]
        self.assertGreater(confirmed["buyer_confirmed_at"], 0)
        self.assertEqual(confirmed["confirmed_by"], "buyer")

        db.deal_gate_delete(101)
        self.assertIsNone(db.deal_gate_get(101))

    def test_unknown_dynamic_field_is_ignored(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            **{"gate_status = 'hacked' --": "ignored"},
        )
        self.assertEqual(db.deal_gate_get(101)["gate_status"], "pending")
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_completed_gate_awaiting_seller_confirmation(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
        )
        db.deal_gate_append_seller_toman_admin(
            101, entry_type="text", text="synthetic admin payment"
        )
        self.assertGreater(db.deal_gate_enable_seller_toman_close(101), 0)
        awaiting = db.deal_gate_list_awaiting_seller_toman_confirm()
        self.assertEqual([item["offer_id"] for item in awaiting], [101])


if __name__ == "__main__":
    unittest.main()
