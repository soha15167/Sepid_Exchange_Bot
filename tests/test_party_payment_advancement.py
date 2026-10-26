"""Party confirmations advance real deal state without an admin confirmation."""

from __future__ import annotations

import gc
import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from database import db, receipt_management
from handlers import deal_gate as flow


class PartyPaymentAdvancementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(db, "DB_PATH", str(Path(self.tmp.name) / "test.db")))
        db.ensure_schema()
        for oid in (101, 102):
            db.deal_gate_upsert(
                offer_id=oid, advert_rowid=4000 + oid,
                buyer_telegram_id=10, seller_telegram_id=20,
                gate_status="completed", buyer_response="yes", seller_response="yes",
                buyer_toman_card_sent_at=100,
                buyer_accounts_text="test EUR account",
            )
        self.context = SimpleNamespace(bot=AsyncMock(), user_data={})
        self.stack.enter_context(patch.object(flow, "ADMIN_IDS", [9001]))
        self.stack.enter_context(patch.object(flow, "DEAL_SUPPORT_ADMIN_IDS", []))
        self.stack.enter_context(patch.object(flow, "DEAL_RECEIPT_REVIEWER_IDS", [7001, 7002]))
        self.stack.enter_context(patch.object(flow, "_log"))
        self.stack.enter_context(patch.object(flow, "_receipt_accounting_log"))
        self.stack.enter_context(patch.object(flow, "_buyer_receipt_accounting_cursor", 0))
        self.stack.enter_context(patch.object(flow, "_buyer_expected_rial", return_value=100))
        self.stack.enter_context(patch.object(flow, "_buyer_dealer_name", return_value="test"))
        self.admin_sync = self.stack.enter_context(patch.object(flow, "sync_deal_admin_notification", new=AsyncMock()))
        self.stack.enter_context(patch.object(flow, "_refresh_admin_deal_after_payment_step", new=AsyncMock()))
        self.stack.enter_context(patch.object(flow, "_show_user_main_menu", new=AsyncMock()))
        self.stack.enter_context(patch.object(flow, "_buyer_receipt_reviewer_body", return_value="test receipt"))
        self.broadcast = self.stack.enter_context(patch.object(flow, "_broadcast_buyer_receipt_confirmation", new=AsyncMock()))
        self.stack.enter_context(patch("utils.deal_milestones.notify_euro_settled_seller", new=AsyncMock()))
        self.stack.enter_context(patch("utils.deal_milestones.notify_toman_settled_buyer", new=AsyncMock()))
        self.post = self.stack.enter_context(patch("utils.iran_panel_client.post_transaction", new=Mock(return_value=(True, "ok"))))
        self.release = self.stack.enter_context(patch.object(flow, "_send_buyer_eur_account_to_seller", new=AsyncMock(side_effect=self._deliver_account)))
        self.admin_confirm = self.stack.enter_context(patch.object(flow, "_admin_sensitive_confirmation", new=AsyncMock()))
        flow._buyer_toman_settlement_locks.clear()
        flow._buyer_receipt_copy_locks.clear()

    def tearDown(self):
        self.stack.close()
        flow._buyer_toman_settlement_locks.clear()
        flow._buyer_receipt_copy_locks.clear()
        gc.collect()
        self.tmp.cleanup()

    async def _deliver_account(self, context, oid, gate, **kwargs):
        db.deal_gate_upsert(
            offer_id=oid, advert_rowid=gate["advert_rowid"], buyer_telegram_id=10,
            seller_telegram_id=20, seller_eur_account_sent_at=200,
        )
        return True

    def _query(self, data, uid):
        q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid),
            answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))
        return q, SimpleNamespace(callback_query=q)

    def _euro(self, oid=101, *, removed=False):
        items = db.deal_gate_append_seller_receipt(oid, entry_type="photo", file_id=f"receipt-{oid}")
        index = len(items) - 1
        if removed:
            receipt_management.archive(oid, "euro", index, receipt_management.fingerprint(items[index]), 9001)
        return index

    def _toman(self, amount=100, *, oid=101, status="ready_for_review"):
        items = db.deal_gate_append_buyer_receipt(oid, entry_type="photo", file_id=f"toman-{len(db.deal_gate_buyer_receipt_list(oid))}")
        index = len(items) - 1
        db.deal_gate_update_buyer_receipt(
            oid, index, amount_rial=amount, accounting_status=status,
            bank_name="test", transfer_type="normal", jdate="1405/07/18",
            reviewer_notify_mids={"7001": 501 + index, "7002": 601 + index},
        )
        return index

    async def _click_toman(self, index=0, *, uid=7001, oid=101):
        receipt = db.deal_gate_buyer_receipt_list(oid)[index]
        q, update = self._query(f"adm|tomset|{oid}|{index}|{receipt['amount_rial']}", uid)
        await flow.deal_admin_toman_settled_callback(update, self.context)
        return q

    async def test_buyer_full_confirmation_advances_all_receipts_only_in_selected_deal(self):
        self._euro(removed=True)
        self._euro()
        self._euro()
        self._euro(102)
        data = flow._buyer_euro_settled_keyboard(101, 2).inline_keyboard[0][0].callback_data
        self.assertLessEqual(len(data.encode()), 64)
        q, update = self._query(data, 10)
        await flow.deal_gate_callback(update, self.context)
        receipts = db.deal_gate_seller_receipt_list(101)
        self.assertFalse(receipts[0]["buyer_confirmed_at"])
        self.assertTrue(all(r["buyer_confirmed_at"] and r["confirmed_by"] == "buyer" for r in receipts[1:]))
        due = db.deal_gate_get(101)["viewer_toman_due_at"]
        self.assertGreater(int(due), 0)
        self.assertFalse(db.deal_gate_seller_receipt_list(102)[0]["buyer_confirmed_at"])
        await flow.deal_gate_callback(update, self.context)
        self.assertEqual(db.deal_gate_get(101)["viewer_toman_due_at"], due)
        self.admin_confirm.assert_not_awaited()

    async def test_legacy_buyer_button_explicitly_requests_full_confirmation(self):
        self._euro()
        self._euro()
        q, update = self._query("deal|eurset|101|0", 10)
        await flow.deal_gate_callback(update, self.context)
        self.assertFalse(any(r["buyer_confirmed_at"] for r in db.deal_gate_seller_receipt_list(101)))
        q.data = q.message.edit_reply_markup.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        await flow.deal_gate_callback(update, self.context)
        self.assertGreater(int(db.deal_gate_get(101)["viewer_toman_due_at"]), 0)

    async def test_new_receipt_invalidates_buyer_snapshot_and_refreshes_button(self):
        self._euro()
        q, update = self._query(flow._buyer_euro_settled_keyboard(101, 0).inline_keyboard[0][0].callback_data, 10)
        self._euro()
        await flow.deal_gate_callback(update, self.context)
        self.assertFalse(any(r["buyer_confirmed_at"] for r in db.deal_gate_seller_receipt_list(101)))
        self.assertNotEqual(q.data, q.message.edit_reply_markup.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data)

    async def test_foreign_buyer_and_closed_deal_cannot_confirm(self):
        self._euro()
        data = flow._buyer_euro_settled_keyboard(101, 0).inline_keyboard[0][0].callback_data
        for uid, status in ((30, "completed"), (10, "closed")):
            db.deal_gate_upsert(offer_id=101, advert_rowid=4101, buyer_telegram_id=10, seller_telegram_id=20, gate_status=status)
            q, update = self._query(data, uid)
            await flow.deal_gate_callback(update, self.context)
        self.assertFalse(db.deal_gate_seller_receipt_list(101)[0]["buyer_confirmed_at"])

    def test_atomic_full_confirmation_rechecks_buyer_identity(self):
        self._euro()
        revision = db.deal_gate_seller_receipts_revision(db.deal_gate_seller_receipt_list(101))
        self.assertFalse(db.deal_gate_confirm_all_seller_receipts(101, expected_revision=revision, confirmed_by="buyer", buyer_telegram_id=30))
        self.assertFalse(db.deal_gate_seller_receipt_list(101)[0]["buyer_confirmed_at"])

    async def test_euro_admin_ui_failure_does_not_block_buyer_advancement(self):
        self._euro()
        self.admin_sync.side_effect = RuntimeError("UI unavailable")
        q, update = self._query(flow._buyer_euro_settled_keyboard(101, 0).inline_keyboard[0][0].callback_data, 10)
        await flow.deal_gate_callback(update, self.context)
        self.assertGreater(int(db.deal_gate_get(101)["viewer_toman_due_at"]), 0)
        self.assertIsNone(q.message.edit_reply_markup.await_args.kwargs["reply_markup"])

    async def test_either_reviewer_can_settle_exact_total_without_admin(self):
        self._toman(40)
        self._toman(60)
        self._toman(100, oid=102)
        await self._click_toman(0)
        self.release.assert_not_awaited()
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["payload"]["iran_amount"], 40)
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "submitted")
        await self._click_toman(1, uid=7002)
        self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
        self.assertEqual(int(db.deal_gate_get(101)["seller_eur_account_sent_at"]), 200)
        self.assertTrue(all(r["accounting_status"] == "submitted" for r in db.deal_gate_buyer_receipt_list(101)))
        self.assertFalse(db.deal_gate_buyer_receipt_list(102)[0].get("reviewer_received_at"))
        self.admin_confirm.assert_not_awaited()
        self.assertEqual(self.post.call_count, 2)
        await self._click_toman(1, uid=7002)
        self.assertEqual(self.post.call_count, 2)
        self.release.assert_awaited_once()

    async def test_unknown_panel_result_keeps_payment_and_never_resubmits(self):
        self._toman()
        self.post.side_effect = [(False, "unavailable"), (True, "ok")]
        q = await self._click_toman()
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        self.assertGreater(receipt["reviewer_received_at"], 0)
        settled = db.deal_gate_get(101)["buyer_toman_settled_at"]
        self.assertGreater(int(settled), 0)
        self.release.assert_awaited_once()
        self.assertIsNone(q.message.edit_reply_markup.await_args.kwargs["reply_markup"])
        await self._click_toman(uid=7002)
        self.assertEqual(db.deal_gate_get(101)["buyer_toman_settled_at"], settled)
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "panel_unknown")
        self.post.assert_called_once()
        # Resolving bookkeeping later neither repeats delivery nor resubmits money.
        db.deal_gate_update_buyer_receipt(101, 0, accounting_status="submitted")
        await self._click_toman(uid=7002)
        self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
        self.admin_confirm.assert_not_awaited()
        self.broadcast.assert_not_awaited()

    async def test_missing_bank_metadata_does_not_block_confirmed_payment(self):
        self._toman(status="review")
        db.deal_gate_update_buyer_receipt(101, 0, bank_name="", transfer_type="")
        await self._click_toman()
        gate = db.deal_gate_get(101)
        self.assertGreater(int(gate["buyer_toman_settled_at"]), 0)
        self.assertEqual(int(gate["seller_eur_account_sent_at"]), 200)
        self.post.assert_not_called()
        self.admin_confirm.assert_not_awaited()

    async def test_account_delivery_precedes_remote_bookkeeping(self):
        self._toman()

        def unavailable(**kwargs):
            self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
            self.release.assert_awaited_once()
            raise RuntimeError("ledger offline")

        self.post.side_effect = unavailable
        await self._click_toman()
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "panel_unknown")
        self.assertEqual(int(db.deal_gate_get(101)["seller_eur_account_sent_at"]), 200)

    async def test_sweep_recovers_old_confirmations_and_books_other_installments(self):
        self._toman(status="review")
        db.deal_gate_update_buyer_receipt(101, 0, bank_name="")
        self._toman(oid=102)
        for oid in (101, 102):
            db.deal_gate_confirm_buyer_receipt_received(oid, 0, 7001)
        await flow.run_received_buyer_receipt_sweep(self.context.bot)
        for oid in (101, 102):
            self.assertGreater(int(db.deal_gate_get(oid)["buyer_toman_settled_at"]), 0)
            self.assertEqual(int(db.deal_gate_get(oid)["seller_eur_account_sent_at"]), 200)
        self.post.assert_called_once()
        self.assertEqual(db.deal_gate_buyer_receipt_list(102)[0]["accounting_status"], "submitted")
        await flow.run_received_buyer_receipt_sweep(self.context.bot)
        self.assertEqual(self.release.await_count, 2)
        self.post.assert_called_once()

    async def test_one_incomplete_receipt_does_not_starve_other_bookkeeping(self):
        self._toman(40, status="review")
        db.deal_gate_update_buyer_receipt(101, 0, bank_name="")
        self._toman(60)
        for index in (0, 1):
            db.deal_gate_confirm_buyer_receipt_received(101, index, 7001)
        await flow.run_received_buyer_receipt_sweep(self.context.bot)
        self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["payload"]["iran_amount"], 60)

    async def test_slow_bookkeeping_job_does_not_block_queued_telegram_delivery(self):
        import main
        self._toman()
        db.deal_gate_confirm_buyer_receipt_received(101, 0, 7001)
        jobs = {}

        def register(callback, **kwargs):
            jobs[kwargs["name"]] = callback

        app = SimpleNamespace(job_queue=SimpleNamespace(run_repeating=register))
        main._setup_deal_delivery_retry_job(app)
        main._setup_received_buyer_receipt_job(app)
        entered, unblock = asyncio.Event(), asyncio.Event()

        async def slow_ledger(*args, **kwargs):
            entered.set()
            await unblock.wait()
            return True

        self.context.bot.send_message.return_value = SimpleNamespace(message_id=999)
        with patch.object(flow, "_sync_received_buyer_accounting", side_effect=slow_ledger):
            task = asyncio.create_task(jobs["received_buyer_receipt_sweep"](self.context))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                queued = db.deal_delivery_enqueue(
                    offer_id=101, recipient_telegram_id=10, party="buyer",
                    tag="test receipt", payload_type="text",
                    payload={"body_html": "test receipt"}, dedupe_key="test:slow-ledger",
                )
                await asyncio.wait_for(jobs["deal_delivery_retry_sweep"](self.context), timeout=5)
                current = next(
                    row for row in db.deal_delivery_list_for_offer(101) if row["id"] == queued["id"]
                )
                self.assertEqual(current["status"], "sent")
                self.assertFalse(task.done())
            finally:
                unblock.set()
                await task

    async def test_slow_bookkeeping_does_not_lock_an_already_received_payment(self):
        self._toman()
        entered, unblock = asyncio.Event(), asyncio.Event()

        async def slow_ledger(*args, **kwargs):
            entered.set()
            await unblock.wait()
            return True

        with patch.object(flow, "_sync_received_buyer_accounting", side_effect=slow_ledger):
            task = asyncio.create_task(self._click_toman())
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                q = await asyncio.wait_for(self._click_toman(uid=7002), timeout=5)
                self.assertIn("قبلاً", q.answer.await_args.args[0])
                self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
                self.release.assert_awaited_once()
            finally:
                unblock.set()
                await task

    async def test_received_payment_metadata_can_be_completed_after_settlement(self):
        from handlers import receipt_management as management
        self._toman(status="review")
        db.deal_gate_update_buyer_receipt(101, 0, bank_name="")
        await self._click_toman()
        gate = db.deal_gate_get(101)
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        self.context.user_data[management._FIELDS_KEY] = {
            "offer_id": 101, "index": 0, "actor_id": 9001,
            "expected": receipt_management.fingerprint(receipt),
        }
        message = SimpleNamespace(text="test | normal | 1405/07/20", reply_text=AsyncMock())
        with patch.object(management, "allowed", return_value=True):
            await management.message(
                SimpleNamespace(message=message, effective_user=SimpleNamespace(id=9001)),
                self.context,
            )
        self.assertEqual(db.deal_gate_get(101)["buyer_toman_settled_at"], gate["buyer_toman_settled_at"])
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "submitted")
        self.post.assert_called_once()
        self.release.assert_awaited_once()

    async def test_confirmed_reviewer_copies_keep_controls_until_account_is_delivered(self):
        self._toman()
        receipt, _ = db.deal_gate_confirm_buyer_receipt_received(101, 0, 7001)
        for settled, sent, retains_button in (("0", "0", True), ("100", "0", True), ("100", "200", False)):
            db.deal_gate_upsert(offer_id=101, advert_rowid=4101, buyer_telegram_id=10, seller_telegram_id=20,
                buyer_toman_settled_at=settled, seller_eur_account_sent_at=sent)
            await flow._sync_buyer_receipt_reviewer_messages(self.context.bot, offer_id=101, receipt_index=0, receipt=receipt)
            markup = self.context.bot.edit_message_caption.await_args.kwargs["reply_markup"]
            self.assertEqual(markup is not None, retains_button)

    async def test_delivery_failure_preserves_settlement_and_reviewer_retries_without_reposting(self):
        self._toman()
        self.release.side_effect = None
        self.release.return_value = False
        q = await self._click_toman()
        settled = db.deal_gate_get(101)["buyer_toman_settled_at"]
        self.assertGreater(int(settled), 0)
        self.assertIsNotNone(q.message.edit_reply_markup.await_args.kwargs["reply_markup"])
        self.release.side_effect = self._deliver_account
        await self._click_toman(uid=7002)
        self.assertEqual(db.deal_gate_get(101)["buyer_toman_settled_at"], settled)
        self.assertEqual(int(db.deal_gate_get(101)["seller_eur_account_sent_at"]), 200)
        self.post.assert_called_once()
        self.admin_confirm.assert_not_awaited()

    async def test_notification_failure_does_not_block_reviewer_advancement(self):
        self._toman()
        self.admin_sync.side_effect = RuntimeError("UI unavailable")
        with patch.object(flow, "_sync_buyer_receipt_reviewer_messages", new=AsyncMock(side_effect=RuntimeError("UI unavailable"))):
            await self._click_toman()
        self.assertGreater(int(db.deal_gate_get(101)["buyer_toman_settled_at"]), 0)
        self.release.assert_awaited_once()

    async def test_partial_excess_and_unreadable_payments_stay_pending(self):
        for amount, status in ((40, "ready_for_review"), (120, "ready_for_review"), (0, "processing")):
            with self.subTest(amount=amount):
                db.deal_gate_upsert(offer_id=101, advert_rowid=4101, buyer_telegram_id=10, seller_telegram_id=20, buyer_receipt_log="[]")
                self._toman(amount, status=status)
                await self._click_toman()
                self.assertFalse(db.deal_gate_get(101)["buyer_toman_settled_at"])
        self.assertEqual(self.post.call_count, 2)
        self.release.assert_not_awaited()

    async def test_admin_received_confirmation_automatically_registers_income(self):
        self._toman()
        self.admin_confirm.return_value = True
        q, update = self._query("adm|tomset|101|yes", 9001)
        await flow.deal_admin_toman_settled_callback(update, self.context)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["payload"]["iran_type"], "ورودی")
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "submitted")
        self.admin_confirm.assert_awaited_once()
        self.release.assert_awaited_once()

    def test_db_submission_claim_requires_received_money_and_live_deal(self):
        self._toman()
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(101, 0))
        db.deal_gate_confirm_buyer_receipt_received(101, 0, 7001)
        db.deal_gate_upsert(offer_id=101, advert_rowid=4101, buyer_telegram_id=10, seller_telegram_id=20, gate_status="closed")
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(101, 0))

    async def test_registered_installment_cannot_be_rebooked_by_reupload_or_amount_edit(self):
        from database import receipt_management
        self._toman(40)
        await self._click_toman()
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        self.assertIsNone(receipt_management.edit_buyer_amount(101, 0, receipt_management.fingerprint(receipt), 50, 9001))
        await self._click_toman(uid=7002)
        self.post.assert_called_once()

    async def test_concurrent_reviewers_register_one_installment_once(self):
        self._toman(40)
        await asyncio.gather(self._click_toman(uid=7001), self._click_toman(uid=7002))
        self.post.assert_called_once()
        self.assertEqual(db.deal_gate_buyer_receipt_list(101)[0]["accounting_status"], "submitted")
        self.release.assert_not_awaited()

    async def test_metadata_completion_registers_already_received_money_without_new_confirmation(self):
        from handlers import receipt_management as management
        self._toman(40)
        db.deal_gate_update_buyer_receipt(101, 0, jdate="")
        await self._click_toman()
        self.post.assert_not_called()
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        self.context.user_data[management._FIELDS_KEY] = {
            "offer_id": 101, "index": 0, "actor_id": 9001,
            "expected": receipt_management.fingerprint(receipt),
        }
        message = SimpleNamespace(text="test | normal | 1405/07/20", reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=9001))
        with patch.object(management, "allowed", return_value=True):
            self.assertTrue(await management.message(update, self.context))
        current = db.deal_gate_buyer_receipt_list(101)[0]
        self.assertEqual(current["reviewer_received_at"], receipt["reviewer_received_at"])
        self.assertEqual(current["accounting_status"], "submitted")
        self.post.assert_called_once()
        self.admin_confirm.assert_not_awaited()
        self.release.assert_not_awaited()

    async def test_metadata_completion_cannot_book_unreceived_money(self):
        from handlers import receipt_management as management
        self._toman(40)
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        self.context.user_data[management._FIELDS_KEY] = {
            "offer_id": 101, "index": 0, "actor_id": 9001,
            "expected": receipt_management.fingerprint(receipt),
        }
        message = SimpleNamespace(text="test | normal | 1405/07/20", reply_text=AsyncMock())
        with patch.object(management, "allowed", return_value=True):
            await management.message(SimpleNamespace(message=message, effective_user=SimpleNamespace(id=9001)), self.context)
        self.post.assert_not_called()
        self.assertFalse(db.deal_gate_buyer_receipt_list(101)[0].get("reviewer_received_at"))

    async def test_unknown_submission_cannot_be_reset_by_amount_or_metadata_edit(self):
        self._toman()
        self.post.return_value = (False, "timeout")
        await self._click_toman()
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        expected = receipt_management.fingerprint(receipt)
        self.assertIsNone(receipt_management.edit_buyer_amount(101, 0, expected, 50, 9001))
        self.assertIsNone(receipt_management.edit_buyer_accounting_fields(101, 0, expected, "test", "normal", "1405/07/20", 9001))
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(101, 0))
        await self._click_toman()
        self.post.assert_called_once()

    async def test_legacy_failed_submission_is_not_blindly_retried_or_reset(self):
        self._toman()
        db.deal_gate_confirm_buyer_receipt_received(101, 0, 7001)
        db.deal_gate_update_buyer_receipt(101, 0, accounting_status="panel_failed")
        await self._click_toman()
        receipt = db.deal_gate_buyer_receipt_list(101)[0]
        expected = receipt_management.fingerprint(receipt)
        self.assertIsNone(db.deal_gate_claim_buyer_receipt_submission(101, 0))
        self.assertIsNone(receipt_management.edit_buyer_accounting_fields(101, 0, expected, "test", "normal", "1405/07/20", 9001))
        self.post.assert_not_called()

    async def test_human_click_recovers_unknown_delivery_before_stale_amount_validation(self):
        self._toman()
        db.deal_gate_update_buyer_receipt(101, 0, reviewer_notify_mids={"7002": 601},
            reviewer_notify_delivery={"7001": {"status": "unknown", "at": 1}})
        q, update = self._query("adm|tomset|101|0|0", 7001)
        q.message.message_id, q.message.chat_id = 501, 7001
        await flow.deal_admin_toman_settled_callback(update, self.context)
        current = db.deal_gate_buyer_receipt_list(101)[0]
        self.assertEqual(current["reviewer_notify_mids"]["7001"], 501)
        self.assertEqual(current["reviewer_notify_delivery"]["7001"]["status"], "delivered")
        self.assertFalse(current.get("reviewer_received_at"))
        self.post.assert_not_called()
        q.data = "adm|tomset|101|0|100"
        await flow.deal_admin_toman_settled_callback(update, self.context)
        self.post.assert_called_once()

    async def test_stale_amount_and_unauthorized_actor_cannot_confirm(self):
        self._toman()
        for uid, amount in ((7001, 90), (30, 100)):
            q, update = self._query(f"adm|tomset|101|0|{amount}", uid)
            await flow.deal_admin_toman_settled_callback(update, self.context)
        self.assertFalse(db.deal_gate_buyer_receipt_list(101)[0].get("reviewer_received_at"))
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
