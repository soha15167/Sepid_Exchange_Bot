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

        self.assertTrue(
            db.deal_gate_mark_seller_toman_settled(101, settled_at=123456)
        )
        self.assertFalse(
            db.deal_gate_mark_seller_toman_settled(101, settled_at=123457)
        )
        self.assertEqual(
            int(db.deal_gate_get(101)["seller_toman_settled_at"]), 123456
        )
        self.assertEqual(db.deal_gate_list_awaiting_seller_toman_confirm(), [])

    def test_admin_toman_receipt_reminder_query_tracks_only_unfinished_delivery(self):
        for offer_id, status in (
            (201, "pending"),
            (202, "accounts"),
            (203, "completed"),
            (204, "rejected"),
            (205, "closed"),
        ):
            db.deal_gate_upsert(
                offer_id=offer_id,
                advert_rowid=3200 + offer_id,
                buyer_telegram_id=10,
                seller_telegram_id=20,
                gate_status=status,
            )

        self.assertEqual(
            [
                item["offer_id"]
                for item in db.deal_gate_list_awaiting_admin_toman_receipt()
            ],
            [201, 202, 203],
        )

        db.deal_gate_upsert(
            offer_id=203,
            advert_rowid=3403,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            seller_toman_close_enabled_at=123456,
        )
        self.assertEqual(
            [
                item["offer_id"]
                for item in db.deal_gate_list_awaiting_admin_toman_receipt()
            ],
            [201, 202],
        )

    def test_admin_toman_receipt_reminders_exclude_deals_before_activation(self):
        self._create_gate()
        with sqlite3.connect(self.path) as conn:
            cutoff = int(
                conn.execute(
                    """
                    SELECT value FROM settings
                    WHERE key = 'admin_toman_reminder_cutoff_at'
                    """
                ).fetchone()[0]
            )
            conn.execute(
                "UPDATE offer_deal_gates SET started_at = ? WHERE offer_id = 101",
                (cutoff - 1,),
            )
            conn.commit()

        self.assertEqual(db.deal_gate_list_awaiting_admin_toman_receipt(), [])

        db.deal_gate_upsert(
            offer_id=102,
            advert_rowid=3197,
            buyer_telegram_id=11,
            seller_telegram_id=21,
            gate_status="pending",
        )
        self.assertEqual(
            [
                item["offer_id"]
                for item in db.deal_gate_list_awaiting_admin_toman_receipt()
            ],
            [102],
        )

    def test_admin_outbound_log_party_is_preserved_for_persistent_timing(self):
        self._create_gate()
        db.bot_outbound_log_insert(
            101,
            7001,
            "admin",
            "synthetic hourly reminder",
            body_html="reminder",
        )
        rows = db.bot_outbound_log_list(101)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["party"], "admin")

    def test_selected_offer_remains_linked_to_gate_until_reactivation(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO advert_offers (
                    id, advert_rowid, proposer_telegram_id, rate_toman,
                    created_at, status, seq_in_advert
                ) VALUES (101, 3196, 20, 205000, '2026-07-18', 'accepted', 1)
                """
            )
            conn.commit()
        self._create_gate()

        selected = db.list_accepted_offers_for_advert(3196)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["gate_status"], "pending")

        db.update_advert_offer_status(101, "gate_rejected")
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="rejected",
        )
        selected = db.list_accepted_offers_for_advert(3196)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["gate_status"], "rejected")

        db.update_advert_offer_status(101, "gate_aborted")
        db.deal_gate_delete(101)
        self.assertEqual(db.list_accepted_offers_for_advert(3196), [])

    def test_all_account_deals_for_same_user_are_returned_separately(self):
        db.deal_gate_upsert(
            offer_id=201,
            advert_rowid=3201,
            buyer_telegram_id=10,
            seller_telegram_id=21,
            gate_status="accounts",
        )
        db.deal_gate_upsert(
            offer_id=202,
            advert_rowid=3202,
            buyer_telegram_id=10,
            seller_telegram_id=22,
            gate_status="accounts",
        )

        rows = db.deal_gate_accounts_for_user(10)

        self.assertEqual({int(item["offer_id"]) for item in rows}, {201, 202})
        db.deal_gate_upsert(
            offer_id=201,
            advert_rowid=3201,
            buyer_telegram_id=10,
            seller_telegram_id=21,
            buyer_accounts_text="synthetic account",
        )
        remaining = db.deal_gate_accounts_for_user(10)
        self.assertEqual([int(item["offer_id"]) for item in remaining], [202])


if __name__ == "__main__":
    unittest.main()
