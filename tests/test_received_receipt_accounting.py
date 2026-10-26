"""SQLite safeguards for bookkeeping after received money advances a deal."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from database import db, receipt_management as store


class ReceivedReceiptAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "received.db")
        patcher = patch.object(db, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.ensure_schema()

    def _gate(self, *, oid=836, stage="completed", settled=100, sent=200,
              received=50, status="review", **receipt_fields):
        receipt = {
            "type": "photo", "file_id": "received-receipt",
            "amount_rial": 1_457_250_000, "recognized_amount_rial": 1_457_250_000,
            "reviewer_received_at": received, "reviewer_received_by": 7001,
            "accounting_status": status, "bank_name": "",
            "transfer_type": "", "jdate": "",
            **receipt_fields,
        }
        db.deal_gate_upsert(
            offer_id=oid, advert_rowid=3838, buyer_telegram_id=10,
            seller_telegram_id=20, gate_status=stage,
            buyer_toman_settled_at=settled, seller_eur_account_sent_at=sent,
            buyer_receipt_log=json.dumps([receipt]),
        )
        return receipt

    def _edit(self, receipt, *, oid=836):
        return store.edit_buyer_accounting_fields(
            oid, 0, store.fingerprint(receipt), "ملت", "پایا", "1405/07/26", 9001,
        )

    def test_metadata_can_complete_after_settlement_and_closure_without_changing_money(self):
        for stage in ("completed", "closed"):
            with self.subTest(stage=stage):
                before = self._gate(stage=stage)
                edited = self._edit(before)
                self.assertIsNotNone(edited)
                for field in ("amount_rial", "recognized_amount_rial",
                              "reviewer_received_at", "reviewer_received_by"):
                    self.assertEqual(edited[field], before[field])
                self.assertEqual(edited["accounting_status"], "ready_for_review")
                self.assertEqual(edited["accounting_field_edit_history"][0]["actor"], 9001)
                gate = db.deal_gate_get(836)
                self.assertEqual(gate["gate_status"], stage)
                self.assertEqual(gate["buyer_toman_settled_at"], "100")
                self.assertEqual(gate["seller_eur_account_sent_at"], "200")

    def test_unreceived_metadata_can_be_prepared_only_before_settlement(self):
        receipt = self._gate(settled=0, received=0)
        edited = self._edit(receipt)
        self.assertIsNotNone(edited)
        self.assertEqual(edited["reviewer_received_at"], 0)
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))
        for stage in ("completed", "closed"):
            with self.subTest(stage=stage):
                receipt = self._gate(stage=stage, received=0)
                self.assertIsNone(self._edit(receipt))

    def test_pre_payment_and_rejected_stages_cannot_prepare_or_claim_bookkeeping(self):
        for stage, settled in (("pending", 0), ("accounts", 0),
                               ("rejected", 100), ("closed", 0)):
            with self.subTest(stage=stage):
                receipt = self._gate(stage=stage, settled=settled,
                                     status="ready_for_review", bank_name="ملت")
                self.assertIsNone(self._edit(receipt))
                self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))

    def test_metadata_edit_and_submission_preserve_uncertain_and_final_outcomes(self):
        for status in ("submitted", "submitting", "panel_unknown", "panel_failed",
                       "duplicate", "rejected"):
            with self.subTest(status=status):
                receipt = self._gate(stage="closed", status=status, bank_name="ملت")
                self.assertIsNone(self._edit(receipt))
                self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))
                self.assertEqual(db.deal_gate_buyer_receipt_list(836)[0], receipt)

    def test_archived_receipt_cannot_be_edited_or_claimed_after_closure(self):
        receipt = self._gate(stage="closed", status="ready_for_review",
                             bank_name="ملت", attachment_removed_at=60)
        self.assertIsNone(self._edit(receipt))
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))

    def test_settled_amount_remains_immutable_when_metadata_is_editable(self):
        for stage in ("completed", "closed"):
            with self.subTest(stage=stage):
                receipt = self._gate(stage=stage)
                self.assertIsNone(store.edit_buyer_amount(
                    836, 0, store.fingerprint(receipt), 1_000, 9001,
                ))
                self.assertEqual(db.deal_gate_buyer_receipt_list(836)[0], receipt)

    def test_stale_metadata_fingerprint_does_not_overwrite_a_prior_correction(self):
        receipt = self._gate(stage="closed")
        edited = self._edit(receipt)
        self.assertIsNotNone(edited)
        self.assertIsNone(self._edit(receipt))
        self.assertEqual(db.deal_gate_buyer_receipt_list(836)[0], edited)

    def test_received_receipt_can_be_claimed_once_before_or_after_settlement_and_closure(self):
        for stage, settled in (("completed", 0), ("completed", 100), ("closed", 100)):
            with self.subTest(stage=stage, settled=settled):
                receipt = self._gate(stage=stage, settled=settled)
                self.assertIsNotNone(self._edit(receipt))
                claimed = db.deal_gate_claim_buyer_receipt_submission(836, 0)
                self.assertEqual(claimed["accounting_status"], "submitting")
                self.assertEqual(claimed["reviewer_received_at"], 50)
                self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))

    def test_concurrent_closed_receipt_claims_have_exactly_one_winner(self):
        receipt = self._gate(stage="closed")
        self._edit(receipt)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(
                lambda _: db.deal_gate_claim_buyer_receipt_submission(836, 0), range(4),
            ))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(db.deal_gate_buyer_receipt_list(836)[0]["accounting_status"], "submitting")

    def test_unreceived_or_zero_amount_receipts_cannot_be_claimed(self):
        for received, amount in ((0, 100), (50, 0)):
            with self.subTest(received=received, amount=amount):
                self._gate(stage="closed", received=received, amount_rial=amount,
                           status="ready_for_review", bank_name="ملت")
                self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(836, 0))

    def test_accounting_batch_advances_past_skipped_rows_and_incomplete_old_receipts(self):
        self._gate(oid=1, received=0)
        self._gate(oid=2, status="panel_unknown")
        self._gate(oid=3)
        self._gate(oid=4, stage="closed")
        self._gate(oid=5, stage="closed", status="submitted")
        self._gate(oid=6, stage="closed", status="panel_unknown")
        self._gate(oid=7, stage="pending")
        self._gate(oid=8, stage="closed", settled=0)
        first, cursor = db.deal_gate_buyer_receipt_accounting_batch(limit=2)
        self.assertEqual(first, [])
        self.assertEqual(cursor, 2)
        second, cursor = db.deal_gate_buyer_receipt_accounting_batch(after_offer_id=cursor, limit=2)
        self.assertEqual([gate["offer_id"] for gate in second], [3, 4])
        self.assertEqual(cursor, 4)
        third, cursor = db.deal_gate_buyer_receipt_accounting_batch(after_offer_id=cursor, limit=2)
        self.assertEqual(third, [])
        self.assertEqual(cursor, 6)
        final, cursor = db.deal_gate_buyer_receipt_accounting_batch(after_offer_id=cursor, limit=2)
        self.assertEqual(final, [])
        self.assertEqual(cursor, 0)

    def test_accounting_batch_includes_stuck_confirmations_and_recorded_delivery_failures(self):
        self._gate(oid=1, settled=0, status="submitted")
        self._gate(oid=2, settled=0, status="panel_unknown")
        self._gate(oid=3, settled=0, status="panel_failed")
        self._gate(oid=4, sent=0, status="submitted")
        self._gate(oid=5, status="submitted")
        self._gate(oid=6, stage="closed", status="submitted")
        self._gate(oid=7, settled=0, status="rejected")
        db.deal_gate_upsert(
            offer_id=8, advert_rowid=3840, buyer_telegram_id=10,
            seller_telegram_id=20, gate_status="completed",
            buyer_toman_settled_at=100,
        )
        gates, cursor = db.deal_gate_buyer_receipt_accounting_batch()
        self.assertEqual([gate["offer_id"] for gate in gates], [1, 2, 3, 4, 8])
        self.assertEqual(cursor, 0)


if __name__ == "__main__":
    unittest.main()
