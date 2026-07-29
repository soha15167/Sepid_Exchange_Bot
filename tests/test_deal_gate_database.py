"""Integration tests for the financial deal-gate state stored in SQLite."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

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

    def _create_relational_deal(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO euro_adverts (id, user_id, status)
                VALUES (3196, 20, 'فعال')
                """
            )
            conn.execute(
                """
                INSERT INTO advert_offers (
                    id, advert_rowid, proposer_telegram_id, rate_toman,
                    created_at, status
                ) VALUES (101, 3196, 10, 210000, '2026-07-22', 'accepted')
                """
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

    def test_receipt_source_message_is_idempotent(self):
        self._create_gate()
        first = db.deal_gate_append_buyer_receipt(
            101,
            entry_type="photo",
            file_id="receipt",
            source_message_id=9001,
        )
        second = db.deal_gate_append_buyer_receipt(
            101,
            entry_type="photo",
            file_id="receipt",
            source_message_id=9001,
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, first)

    def test_delivery_queue_deduplicates_and_retries(self):
        first = db.deal_delivery_enqueue(
            offer_id=101,
            recipient_telegram_id=20,
            party="seller",
            tag="receipt",
            payload_type="text",
            payload={"body_html": "hello"},
            dedupe_key="deal:101:seller:receipt:1",
        )
        second = db.deal_delivery_enqueue(
            offer_id=101,
            recipient_telegram_id=20,
            party="seller",
            tag="receipt",
            payload_type="text",
            payload={"body_html": "hello"},
            dedupe_key="deal:101:seller:receipt:1",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(db.deal_delivery_claim(first["id"]))
        self.assertFalse(db.deal_delivery_claim(first["id"]))
        db.deal_delivery_mark_failed(first["id"], "offline")
        with sqlite3.connect(self.path) as conn:
            status, attempts = conn.execute(
                "SELECT status, attempts FROM deal_delivery_queue WHERE id = ?",
                (first["id"],),
            ).fetchone()
        self.assertEqual(status, "failed")
        self.assertEqual(attempts, 1)

    def test_restart_recovers_only_abandoned_delivery_claims(self):
        queued = db.deal_delivery_enqueue(
            offer_id=101,
            recipient_telegram_id=20,
            party="seller",
            tag="restart",
            payload_type="text",
            payload={"body_html": "hello"},
            dedupe_key="deal:101:restart",
        )
        self.assertTrue(db.deal_delivery_claim(queued["id"]))
        self.assertEqual(db.deal_delivery_due(now=10_000), [])
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                UPDATE deal_delivery_queue
                SET status = 'sending', updated_at = 9000
                WHERE id = ?
                """,
                (queued["id"],),
            )
        due = db.deal_delivery_due(now=10_000)
        self.assertEqual([row["id"] for row in due], [queued["id"]])
        with patch("database.db.time.time", return_value=10_000):
            self.assertTrue(db.deal_delivery_claim(queued["id"]))
            self.assertFalse(db.deal_delivery_claim(queued["id"]))

    def test_problem_queue_detects_old_stage_without_notifying_users(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="pending",
        )
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE offer_deal_gates SET started_at = 1000 WHERE offer_id = 101"
            )
        rows = db.deal_gate_list_problems(now=10_000)
        self.assertEqual([row["offer_id"] for row in rows], [101])
        self.assertIn("stuck_pending", rows[0]["problem_issues"])

    def test_successful_seller_receipt_delivery_unlocks_close_once(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
        )
        queued = db.deal_delivery_enqueue(
            offer_id=101,
            recipient_telegram_id=20,
            party="seller",
            tag="receipt",
            payload_type="photo",
            payload={},
            dedupe_key="seller_toman:101:1",
        )
        self.assertTrue(
            db.deal_gate_record_seller_toman_delivery(
                101,
                entry_type="photo",
                file_id="receipt-file",
                delivery_key="seller_toman:101:1",
                queue_delivery_id=queued["id"],
                telegram_message_id=77,
            )
        )
        self.assertTrue(
            db.deal_gate_record_seller_toman_delivery(
                101,
                entry_type="photo",
                file_id="receipt-file",
                delivery_key="seller_toman:101:1",
            )
        )
        gate = db.deal_gate_get(101)
        self.assertGreater(int(gate["seller_toman_close_enabled_at"]), 0)
        self.assertEqual(len(db.deal_gate_seller_toman_admin_list(101)), 1)
        with sqlite3.connect(self.path) as conn:
            status = conn.execute(
                "SELECT status FROM deal_delivery_queue WHERE id = ?",
                (queued["id"],),
            ).fetchone()[0]
        self.assertEqual(status, "sent")

    def test_reactivation_archives_gate_before_removal(self):
        self._create_gate()
        self.assertTrue(db.deal_gate_archive_and_reactivate(101, 3196))
        self.assertIsNone(db.deal_gate_get(101))
        with sqlite3.connect(self.path) as conn:
            archived = conn.execute(
                "SELECT reason, snapshot_json FROM offer_deal_gate_archive WHERE offer_id = 101"
            ).fetchone()
        self.assertEqual(archived[0], "admin_reactivated")
        self.assertIn('"offer_id": 101', archived[1])

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

    def test_admin_can_atomically_settle_seller_without_recorded_receipt(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
        )

        self.assertFalse(
            db.deal_gate_mark_seller_toman_settled(101, settled_at=123456)
        )
        self.assertTrue(
            db.deal_gate_mark_seller_toman_settled(
                101,
                settled_at=123456,
                require_receipt=False,
            )
        )
        self.assertEqual(
            int(db.deal_gate_get(101)["seller_toman_settled_at"]),
            123456,
        )

    def test_settlement_and_all_close_statuses_commit_atomically(self):
        self._create_relational_deal()
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
        )
        db.deal_gate_append_seller_toman_admin(
            101, entry_type="text", text="delivered receipt"
        )
        db.deal_gate_enable_seller_toman_close(101)

        self.assertTrue(
            db.deal_gate_settle_and_close_atomic(
                101, 3196, settled_at=123456, require_receipt=True
            )
        )
        self.assertFalse(
            db.deal_gate_settle_and_close_atomic(
                101, 3196, settled_at=123457, require_receipt=True
            )
        )
        gate = db.deal_gate_get(101)
        self.assertEqual(gate["gate_status"], "closed")
        self.assertEqual(int(gate["seller_toman_settled_at"]), 123456)
        with sqlite3.connect(self.path) as conn:
            offer_status = conn.execute(
                "SELECT status FROM advert_offers WHERE id = 101"
            ).fetchone()[0]
            advert_status = conn.execute(
                "SELECT status FROM euro_adverts WHERE rowid = 3196"
            ).fetchone()[0]
        self.assertEqual(offer_status, "gate_closed")
        self.assertEqual(advert_status, "بسته")

    def test_concurrent_settlement_has_exactly_one_winner(self):
        self._create_relational_deal()
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="completed",
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda stamp: db.deal_gate_settle_and_close_atomic(
                        101,
                        3196,
                        settled_at=stamp,
                        require_receipt=False,
                    ),
                    (123456, 123457),
                )
            )
        self.assertEqual(sorted(results), [False, True])

    def test_privacy_retention_redacts_financial_artifacts_only_after_cutoff(self):
        self._create_gate()
        db.deal_gate_upsert(
            offer_id=101,
            advert_rowid=3196,
            buyer_telegram_id=10,
            seller_telegram_id=20,
            gate_status="closed",
            buyer_accounts_text="buyer iban",
            seller_accounts_text="seller iban",
            buyer_receipt_log='[{"file_id":"buyer"}]',
            seller_receipt_log='[{"file_id":"seller"}]',
            seller_toman_admin_log='[{"file_id":"admin"}]',
            seller_toman_settled_at=1000,
        )
        self.assertEqual(
            db.deal_privacy_redact_expired(
                now=1000 + 179 * 86400, retention_days=180
            ),
            0,
        )
        self.assertEqual(
            db.deal_privacy_redact_expired(
                now=1000 + 181 * 86400, retention_days=180
            ),
            1,
        )
        gate = db.deal_gate_get(101)
        self.assertEqual(gate["buyer_accounts_text"], "[redacted]")
        self.assertEqual(gate["seller_receipt_log"], "[]")
        self.assertEqual(
            db.deal_privacy_redact_expired(
                now=1000 + 182 * 86400, retention_days=180
            ),
            0,
        )

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
            telegram_message_id=8123,
        )
        rows = db.bot_outbound_log_list(101)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["party"], "admin")
        self.assertEqual(rows[0]["telegram_message_id"], 8123)

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
