"""A receipt has one tracked reviewer copy through uploads and confirmation."""

from __future__ import annotations

import asyncio
import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, TimedOut

from handlers import deal_gate as flow


class ReceiptCopyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.receipt = {
            "type": "photo", "file_id": "original-file", "file_unique_id": "unique-file",
            "source_message_id": 111, "amount_rial": 100,
            "accounting_status": "ready_for_review",
        }
        self.gate = {
            "offer_id": 41, "advert_rowid": 3804, "gate_status": "completed",
            "buyer_toman_settled_at": 0, "seller_eur_account_sent_at": 0,
        }
        self.next_mid = 1000
        self.bot = SimpleNamespace(
            send_photo=AsyncMock(side_effect=self._send),
            send_document=AsyncMock(side_effect=self._send),
            send_message=AsyncMock(side_effect=self._send),
            edit_message_caption=AsyncMock(), edit_message_text=AsyncMock(),
            delete_message=AsyncMock(),
        )
        self.stack.enter_context(patch.object(flow, "DEAL_RECEIPT_REVIEWER_IDS", [7001, 7002, 9001]))
        self.stack.enter_context(patch.object(flow, "ADMIN_IDS", [9001]))
        self.stack.enter_context(patch.object(flow, "deal_gate_get", side_effect=lambda _oid: dict(self.gate)))
        self.stack.enter_context(patch.object(flow, "get_advert_offer_joined", return_value={"advert_rowid": 3804}))
        self.stack.enter_context(patch.object(flow, "deal_gate_buyer_receipt_list", side_effect=lambda _oid: [copy.deepcopy(self.receipt)]))
        self.update = self.stack.enter_context(patch.object(flow, "deal_gate_update_buyer_receipt", side_effect=self._update))
        self.confirm = self.stack.enter_context(patch.object(flow, "deal_gate_confirm_buyer_receipt_received", side_effect=self._confirm))
        flow._buyer_receipt_copy_locks.clear()

    def tearDown(self):
        self.stack.close()
        flow._buyer_receipt_copy_locks.clear()

    async def _send(self, *_args, **_kwargs):
        self.next_mid += 1
        return SimpleNamespace(message_id=self.next_mid)

    def _update(self, _oid, _index, **fields):
        self.receipt.update(copy.deepcopy(fields))
        return copy.deepcopy(self.receipt)

    def _confirm(self, _oid, _index, reviewer_id):
        changed = not bool(self.receipt.get("reviewer_received_at"))
        if changed:
            self.receipt.update(reviewer_received_at=100, reviewer_received_by=reviewer_id)
        return copy.deepcopy(self.receipt), changed

    async def _notify(self):
        await flow._notify_buyer_toman_receipt_reviewers(
            self.bot, offer_id=41, gate=self.gate, receipt_index=0,
            entry_type="photo", file_id="uploaded-again",
        )

    async def _sync(self, receipt=None):
        await flow._sync_buyer_receipt_reviewer_messages(
            self.bot, offer_id=41, receipt_index=0,
            receipt=copy.deepcopy(self.receipt) if receipt is None else receipt,
        )

    def _assert_no_new_copies(self):
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_first_confirmation_and_retry_edit_original_without_broadcast(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51, "7002": 52}
        _receipt, changed = await flow._confirm_and_announce_buyer_receipt(
            self.bot, offer_id=41, receipt_index=0, reviewer_id=7001
        )
        self.assertTrue(changed)
        _receipt, changed_again = await flow._confirm_and_announce_buyer_receipt(
            self.bot, offer_id=41, receipt_index=0, reviewer_id=7002
        )
        self.assertFalse(changed_again)
        self._assert_no_new_copies()
        self.assertEqual(self.bot.edit_message_caption.await_count, 4)
        self.assertEqual(self.receipt["reviewer_received_by"], 7001)
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 51, "7002": 52})
        for call in self.bot.edit_message_caption.await_args_list:
            self.assertIn("دریافت وجه تأیید شد", call.kwargs["caption"])
            self.assertIsNotNone(call.kwargs["reply_markup"])

    async def test_reused_receipt_upload_edits_tracked_copies_without_more_sends(self):
        await self._notify()
        first_mids = copy.deepcopy(self.receipt["reviewer_notify_mids"])
        await self._notify()
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertEqual(self.bot.edit_message_caption.await_count, 2)
        self.assertEqual(self.receipt["reviewer_notify_mids"], first_mids)
        for call in self.bot.send_photo.await_args_list:
            self.assertEqual(call.args[1], "original-file")
        self.assertEqual(set(first_mids), {"7001", "7002"})

    async def test_only_missing_reviewer_receives_new_copy(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51}
        await self._notify()
        self.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(self.bot.edit_message_caption.await_args.kwargs["message_id"], 51)
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.bot.send_photo.await_args.args[0], 7002)
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 51, "7002": 1001})

    async def test_document_upload_also_reuses_its_tracked_copies(self):
        self.receipt["type"] = "document"
        await self._notify()
        first_mids = copy.deepcopy(self.receipt["reviewer_notify_mids"])
        await self._notify()
        self.assertEqual(self.bot.send_document.await_count, 2)
        self.assertEqual(self.bot.edit_message_caption.await_count, 2)
        self.assertEqual(self.receipt["reviewer_notify_mids"], first_mids)
        self.bot.send_photo.assert_not_awaited()

    async def test_deleted_text_copy_is_replaced_with_same_details_and_controls(self):
        self.receipt.update(
            type="text", text="tracking details", receipt_description="tracking details",
            reviewer_received_at=100, reviewer_notify_mids={"7001": 51},
        )
        self.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        await self._sync()
        self.bot.send_message.assert_awaited_once()
        self.assertIn("tracking details", self.bot.send_message.await_args.args[1])
        self.assertIn("دریافت وجه تأیید شد", self.bot.send_message.await_args.args[1])
        self.assertIsNotNone(self.bot.send_message.await_args.kwargs["reply_markup"])
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 1001})
        self.bot.send_photo.assert_not_awaited()

    async def test_reused_confirmed_receipt_preserves_finished_state_for_missing_copy(self):
        self.receipt.update(reviewer_received_at=100, reviewer_received_by=7001,
                            reviewer_notify_mids={"7001": 51})
        self.gate.update(buyer_toman_settled_at=100, seller_eur_account_sent_at=100)
        await self._notify()
        self.assertIn("دریافت وجه تأیید شد", self.bot.send_photo.await_args.kwargs["caption"])
        self.assertIsNone(self.bot.send_photo.await_args.kwargs["reply_markup"])
        self.assertIsNone(self.bot.edit_message_caption.await_args.kwargs["reply_markup"])
        self.assertEqual(self.receipt["reviewer_received_at"], 100)

    async def test_pending_account_delivery_preserves_continuation_button(self):
        self.receipt.update(reviewer_received_at=100, reviewer_notify_mids={"7001": 51})
        self.gate["buyer_toman_settled_at"] = 100
        await self._notify()
        for call in [self.bot.send_photo.await_args, self.bot.edit_message_caption.await_args]:
            buttons = [button for row in call.kwargs["reply_markup"].inline_keyboard for button in row]
            self.assertEqual(buttons[0].callback_data, "adm|tomset|41|0|100")
            self.assertIn("ادامه", buttons[0].text)

    async def test_deleted_message_recreates_only_that_copy_and_tracks_replacement(self):
        self.receipt.update(reviewer_received_at=100, reviewer_received_by=7001,
                            reviewer_notify_mids={"7001": 51, "7002": 52})
        copy_fields = {"reviewer_notify_mids", "reviewer_notify_delivery"}
        financial_before = {key: value for key, value in self.receipt.items() if key not in copy_fields}

        async def edit(**kwargs):
            if kwargs["chat_id"] == 7001:
                raise BadRequest("Message to edit not found")

        self.bot.edit_message_caption.side_effect = edit
        await self._sync()
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.bot.send_photo.await_args.args[0], 7001)
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 1001, "7002": 52})
        self.assertEqual({key: value for key, value in self.receipt.items() if key not in copy_fields}, financial_before)
        self.bot.delete_message.assert_not_awaited()

    async def test_timeout_does_not_create_duplicate_or_overwrite_copy_map(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51, "7002": 52}
        before = copy.deepcopy(self.receipt)
        self.bot.edit_message_caption.side_effect = TimedOut("offline")
        await self._notify()
        self._assert_no_new_copies()
        self.assertEqual(self.receipt, before)
        self.update.assert_not_called()

    async def test_unchanged_caption_does_not_resend_receipt(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51, "7002": 52}
        self.bot.edit_message_caption.side_effect = BadRequest("Message is not modified")
        await self._notify()
        self._assert_no_new_copies()
        self.update.assert_not_called()

    async def test_concurrent_upload_notifications_keep_one_copy_per_reviewer(self):
        await asyncio.gather(self._notify(), self._notify())
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertEqual(self.bot.edit_message_caption.await_count, 2)
        self.assertEqual(set(self.receipt["reviewer_notify_mids"]), {"7001", "7002"})

    async def test_sync_uses_fresh_confirmation_instead_of_stale_ocr_snapshot(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51}
        stale = copy.deepcopy(self.receipt)
        self.receipt.update(reviewer_received_at=100, reviewer_received_by=7001)
        await self._sync(stale)
        self.assertIn("دریافت وجه تأیید شد", self.bot.edit_message_caption.await_args.kwargs["caption"])
        self._assert_no_new_copies()

    async def test_archived_receipt_never_recreates_or_sends_copies(self):
        self.receipt.update(attachment_removed_at=100, reviewer_notify_mids={"7001": 51})
        await self._notify()
        await self._sync()
        self._assert_no_new_copies()
        self.bot.edit_message_caption.assert_not_awaited()
        self.update.assert_not_called()

    async def test_failed_tracking_removes_untracked_new_copy(self):
        def update(_oid, _index, **fields):
            if "reviewer_notify_mids" in fields:
                raise RuntimeError("database unavailable")
            return self._update(_oid, _index, **fields)

        self.update.side_effect = update
        await self._notify()
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertEqual(self.bot.delete_message.await_count, 2)
        self.assertNotIn("reviewer_notify_mids", self.receipt)

    async def test_initial_send_timeout_survives_restart_without_blind_resend(self):
        self.bot.send_photo.side_effect = TimedOut("delivery unknown")
        with patch.object(flow, "DEAL_RECEIPT_REVIEWER_IDS", [7001]):
            await self._notify()
            flow._buyer_receipt_copy_locks.clear()
            await self._notify()
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.receipt["reviewer_notify_delivery"]["7001"]["status"], "unknown")
        self.assertNotIn("reviewer_notify_mids", self.receipt)
        self.assertEqual(self.receipt["amount_rial"], 100)
        self.assertFalse(self.receipt.get("reviewer_received_at"))

    async def test_replacement_timeout_does_not_repeat_unknown_replacement(self):
        self.receipt["reviewer_notify_mids"] = {"7001": 51}
        self.bot.edit_message_caption.side_effect = BadRequest("Message to edit not found")
        self.bot.send_photo.side_effect = TimedOut("delivery unknown")
        await self._sync()
        flow._buyer_receipt_copy_locks.clear()
        await self._sync()
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.bot.edit_message_caption.await_count, 2)
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 51})
        self.assertEqual(self.receipt["reviewer_notify_delivery"]["7001"]["status"], "unknown")

    async def test_interrupted_sending_state_is_not_blindly_sent_again(self):
        self.receipt["reviewer_notify_delivery"] = {"7001": {"status": "sending", "at": 1}}
        with patch.object(flow, "DEAL_RECEIPT_REVIEWER_IDS", [7001]):
            await self._notify()
        self._assert_no_new_copies()

    async def test_unknown_delivery_does_not_prevent_editing_known_copy(self):
        self.receipt.update(
            reviewer_received_at=100, reviewer_notify_mids={"7001": 51},
            reviewer_notify_delivery={"7001": {"status": "unknown", "at": 1}},
        )
        await self._sync()
        self.bot.edit_message_caption.assert_awaited_once()
        self._assert_no_new_copies()

    async def test_human_adopted_copy_is_not_overwritten_when_send_times_out(self):
        async def send(*_args, **_kwargs):
            self.receipt["reviewer_notify_mids"] = {"7001": 77}
            self.receipt["reviewer_notify_delivery"] = {
                "7001": {"status": "delivered", "at": 1, "message_id": 77}
            }
            raise TimedOut("delivery unknown")

        self.bot.send_photo.side_effect = send
        with patch.object(flow, "DEAL_RECEIPT_REVIEWER_IDS", [7001]):
            await self._notify()
        self.assertEqual(self.receipt["reviewer_notify_mids"], {"7001": 77})
        self.assertEqual(self.receipt["reviewer_notify_delivery"]["7001"]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
