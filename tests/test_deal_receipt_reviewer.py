"""Receipt forwarding and narrowly scoped reviewer approval regressions."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ReceiptReviewerTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_proxy_text_receipt_uses_reviewer_ocr_and_admin_sync_pipeline(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 12,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
        }
        message = SimpleNamespace(
            text="بانک ملی مبلغ ۱۰۰٬۰۰۰٬۰۰۰ ریال",
            message_id=501,
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=8001),
        )
        context = SimpleNamespace(
            bot=AsyncMock(),
            user_data={
                deal_gate._DEAL_ADMIN_PXY_KEY: {
                    "offer_id": 41,
                    "advert_rowid": 12,
                    "party": "buyer",
                }
            },
        )
        receipt = {
            "type": "text",
            "source_message_id": 501,
            "accounting_status": "pending",
        }
        with (
            patch.object(deal_gate, "ADMIN_IDS", [8001]),
            patch.object(deal_gate, "deal_gate_get", return_value=gate),
            patch.object(deal_gate, "deal_gate_append_buyer_receipt", return_value=[receipt]),
            patch.object(deal_gate, "_log_receipt_consistency"),
            patch.object(deal_gate, "_log"),
            patch.object(
                deal_gate, "_notify_buyer_toman_receipt_reviewers", new=AsyncMock()
            ) as notify,
            patch.object(
                deal_gate, "_admin_receipt_upload_done", new=AsyncMock()
            ) as admin_sync,
            patch.object(
                deal_gate, "_auto_account_buyer_receipt", new=AsyncMock()
            ) as account,
        ):
            handled = await deal_gate._deal_admin_proxy_receipt_try_message(
                update, context
            )

        self.assertTrue(handled)
        notify.assert_awaited_once()
        admin_sync.assert_awaited_once_with(context.bot, update, 41)
        account.assert_awaited_once()
        self.assertEqual(account.await_args.kwargs["receipt_index"], 0)

    async def test_admin_proxy_photo_receipt_preserves_unique_id_for_ocr(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 12,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
        }
        photo = SimpleNamespace(file_id="file-1", file_unique_id="unique-1")
        message = SimpleNamespace(
            photo=[photo],
            document=None,
            caption="",
            message_id=502,
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=8001),
        )
        context = SimpleNamespace(
            bot=AsyncMock(),
            user_data={
                deal_gate._DEAL_ADMIN_PXY_KEY: {
                    "offer_id": 41,
                    "advert_rowid": 12,
                    "party": "buyer",
                }
            },
        )
        receipt = {
            "type": "photo",
            "file_id": "file-1",
            "file_unique_id": "unique-1",
            "source_message_id": 502,
            "accounting_status": "pending",
        }
        with (
            patch.object(deal_gate, "ADMIN_IDS", [8001]),
            patch.object(deal_gate, "deal_gate_get", return_value=gate),
            patch.object(
                deal_gate, "deal_gate_append_buyer_receipt", return_value=[receipt]
            ) as append,
            patch.object(deal_gate, "_log_receipt_consistency"),
            patch.object(deal_gate, "_log"),
            patch.object(
                deal_gate, "_notify_buyer_toman_receipt_reviewers", new=AsyncMock()
            ) as notify,
            patch.object(
                deal_gate, "_admin_receipt_upload_done", new=AsyncMock()
            ),
            patch.object(
                deal_gate, "_auto_account_buyer_receipt", new=AsyncMock()
            ) as account,
        ):
            handled = await deal_gate._deal_admin_proxy_receipt_try_photo(
                update, context
            )

        self.assertTrue(handled)
        self.assertEqual(append.call_args.kwargs["file_unique_id"], "unique-1")
        notify.assert_awaited_once()
        self.assertEqual(account.await_args.kwargs["file_unique_id"], "unique-1")

    async def test_toman_confirmations_are_serialized_per_deal(self):
        from handlers import deal_gate

        query = SimpleNamespace(
            data="adm|tomset|41|0",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace()
        active = 0
        maximum = 0

        async def inner(_update, _context):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

        deal_gate._buyer_toman_settlement_locks.clear()
        with patch.object(
            deal_gate, "_deal_admin_toman_settled_callback_locked", side_effect=inner
        ):
            await asyncio.gather(
                deal_gate.deal_admin_toman_settled_callback(update, context),
                deal_gate.deal_admin_toman_settled_callback(update, context),
            )

        self.assertEqual(maximum, 1)

    async def test_receipt_photo_is_copied_to_configured_non_admin_reviewer(self):
        from handlers import deal_gate

        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=55))
        )
        receipts = [{}, {}, {"type": "photo", "amount_rial": 120_000_000}]
        with (
            patch.object(deal_gate, "DEAL_RECEIPT_REVIEWER_IDS", [7001, 7002]),
            patch.object(deal_gate, "ADMIN_IDS", [7002]),
            patch.object(deal_gate, "get_advert_offer_joined", return_value={"seq_in_advert": 9}),
            patch.object(deal_gate, "deal_gate_buyer_receipt_list", return_value=receipts),
            patch.object(deal_gate, "deal_gate_update_buyer_receipt") as update_receipt,
        ):
            await deal_gate._notify_buyer_toman_receipt_reviewers(
                bot,
                offer_id=41,
                gate={"offer_id": 41},
                receipt_index=2,
                entry_type="photo",
                text="receipt caption",
                file_id="telegram-file",
            )

        bot.send_photo.assert_awaited_once()
        self.assertEqual(bot.send_photo.await_args.args[:2], (7001, "telegram-file"))
        button = bot.send_photo.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "adm|tomset|41|2|120000000")
        self.assertIn("۱۲۰٬۰۰۰٬۰۰۰", bot.send_photo.await_args.kwargs["caption"])
        update_receipt.assert_called_once_with(
            41, 2, reviewer_notify_mids={"7001": 55}
        )

    def test_multi_receipt_totals_exclude_duplicate_and_rejected_uploads(self):
        from handlers import deal_gate

        receipts = [
            {"accounting_status": "ready_for_review", "amount_rial": 40, "reviewer_received_at": 1},
            {"accounting_status": "submitted", "amount_rial": 60},
            {"accounting_status": "duplicate", "amount_rial": 40},
            {"accounting_status": "rejected", "amount_rial": 900},
        ]
        with patch.object(deal_gate, "_buyer_expected_rial", return_value=100):
            self.assertEqual(
                deal_gate._buyer_toman_reviewer_totals({}, receipts),
                (100, 100, 1, 0),
            )

    async def _run_reviewer_click(self, receipts, *, expected_rial):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 12,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "completed",
            "buyer_toman_card_sent_at": 1,
            "buyer_toman_settled_at": 0,
        }
        query = SimpleNamespace(
            data=f"adm|tomset|41|{len(receipts) - 1}",
            from_user=SimpleNamespace(id=7001),
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(bot=AsyncMock(), user_data={})

        def update_receipt(_oid, index, **fields):
            receipts[index].update(fields)
            return dict(receipts[index])

        def confirm_receipt(_oid, index, reviewer_id):
            if int(receipts[index].get("reviewer_received_at") or 0) > 0:
                return dict(receipts[index]), False
            receipts[index].update(
                reviewer_received_at=100,
                reviewer_received_by=reviewer_id,
            )
            return dict(receipts[index]), True

        deal_gate._buyer_toman_settlement_locks.clear()
        with (
            patch.object(deal_gate, "DEAL_RECEIPT_REVIEWER_IDS", [7001]),
            patch.object(deal_gate, "_deal_gate_allows_admin_payment", return_value=True),
            patch.object(deal_gate, "_buyer_expected_rial", return_value=expected_rial),
            patch.object(deal_gate, "deal_gate_get", return_value=dict(gate)),
            patch.object(deal_gate, "deal_gate_buyer_receipt_list", side_effect=lambda _oid: receipts),
            patch.object(deal_gate, "deal_gate_update_buyer_receipt", side_effect=update_receipt),
            patch.object(
                deal_gate,
                "deal_gate_confirm_buyer_receipt_received",
                side_effect=confirm_receipt,
            ),
            patch.object(deal_gate, "deal_gate_upsert"),
            patch.object(deal_gate, "_admin_sensitive_confirmation", new=AsyncMock()) as confirm,
            patch.object(deal_gate, "_submit_confirmed_buyer_receipts_to_iran", new=AsyncMock(return_value=(True, ""))),
            patch.object(
                deal_gate,
                "_send_buyer_eur_account_to_seller",
                new=AsyncMock(return_value=True),
            ) as release,
            patch.object(deal_gate, "_log"),
            patch("utils.deal_milestones.notify_toman_settled_buyer", new=AsyncMock()),
            patch.object(
                deal_gate,
                "_refresh_admin_deal_after_payment_step",
                new=AsyncMock(),
            ) as refresh,
            patch.object(deal_gate, "sync_deal_admin_notification", new=AsyncMock()) as sync,
        ):
            await deal_gate.deal_admin_toman_settled_callback(
                SimpleNamespace(callback_query=query), context
            )

        return query, confirm, release, refresh, sync

    async def test_exact_multi_receipt_total_releases_account_without_admin_prompt(self):
        receipts = [
            {"accounting_status": "ready_for_review", "amount_rial": 40, "reviewer_received_at": 10},
            {"accounting_status": "ready_for_review", "amount_rial": 60},
        ]
        query, confirm, release, refresh, _sync = await self._run_reviewer_click(
            receipts, expected_rial=100
        )

        confirm.assert_not_awaited()
        release.assert_awaited_once()
        refresh.assert_awaited_once()
        self.assertTrue(receipts[1]["reviewer_received_at"])
        self.assertIsNone(
            query.message.edit_reply_markup.await_args_list[-1].kwargs["reply_markup"]
        )

    async def test_partial_total_updates_admin_but_does_not_release_account(self):
        receipts = [{"accounting_status": "ready_for_review", "amount_rial": 40}]
        query, _confirm, release, refresh, sync = await self._run_reviewer_click(
            receipts, expected_rial=100
        )

        release.assert_not_awaited()
        refresh.assert_not_awaited()
        sync.assert_awaited_once()
        self.assertIn("مانده", query.answer.await_args.args[0])

    async def test_excess_total_requires_main_admin_and_does_not_release_account(self):
        receipts = [{"accounting_status": "ready_for_review", "amount_rial": 120}]
        query, _confirm, release, refresh, sync = await self._run_reviewer_click(
            receipts, expected_rial=100
        )

        release.assert_not_awaited()
        refresh.assert_not_awaited()
        sync.assert_awaited_once()
        self.assertIn("بیشتر", query.answer.await_args.args[0])

    async def test_unreadable_receipt_keeps_reviewer_button_for_retry(self):
        receipts = [{"accounting_status": "processing", "amount_rial": 0}]
        query, _confirm, release, _refresh, sync = await self._run_reviewer_click(
            receipts, expected_rial=100
        )

        release.assert_not_awaited()
        sync.assert_not_awaited()
        query.message.edit_reply_markup.assert_not_awaited()
        self.assertNotIn("reviewer_received_at", receipts[0])

    async def test_confirmation_updates_all_reviewer_copies_with_persian_amount(self):
        from handlers import deal_gate

        receipt = {
            "type": "photo",
            "amount_rial": 120_000_000,
            "reviewer_received_at": 100,
            "reviewer_notify_mids": {"7001": 51, "7002": 52},
        }
        bot = AsyncMock()
        with (
            patch.object(deal_gate, "get_advert_offer_joined", return_value={"seq_in_advert": 9}),
            patch.object(deal_gate, "deal_gate_get", return_value={
                "buyer_toman_settled_at": "100", "seller_eur_account_sent_at": "100",
            }),
        ):
            await deal_gate._sync_buyer_receipt_reviewer_messages(
                bot, offer_id=41, receipt_index=0, receipt=receipt
            )

        self.assertEqual(bot.edit_message_caption.await_count, 2)
        for call in bot.edit_message_caption.await_args_list:
            self.assertIsNone(call.kwargs["reply_markup"])
            self.assertIn("دریافت وجه تأیید شد", call.kwargs["caption"])
            self.assertIn("۱۲۰٬۰۰۰٬۰۰۰", call.kwargs["caption"])

    async def test_text_receipt_details_survive_amount_and_confirmation_sync(self):
        from handlers import deal_gate

        receipt = {
            "type": "text",
            "text": "شماره پیگیری ۱۲۳۴۵۶",
            "receipt_description": "شماره پیگیری ۱۲۳۴۵۶",
            "amount_rial": 80_000_000,
            "reviewer_received_at": 100,
            "reviewer_notify_mids": {"7001": 61, "7002": 62},
        }
        bot = AsyncMock()
        with patch.object(
            deal_gate, "get_advert_offer_joined", return_value={"seq_in_advert": 9}
        ):
            await deal_gate._sync_buyer_receipt_reviewer_messages(
                bot, offer_id=41, receipt_index=0, receipt=receipt
            )

        self.assertEqual(bot.edit_message_text.await_count, 2)
        for call in bot.edit_message_text.await_args_list:
            self.assertIn("شماره پیگیری ۱۲۳۴۵۶", call.kwargs["text"])
            self.assertIn("مبلغ دریافت‌شده", call.kwargs["text"])

    async def test_confirmation_is_broadcast_once_to_configured_reviewers(self):
        from handlers import deal_gate, offers

        bot = AsyncMock()
        receipt = {"amount_rial": 120_000_000, "reviewer_received_at": 100}
        with (
            patch.object(deal_gate, "DEAL_RECEIPT_REVIEWER_IDS", [7001, 7002]),
            patch.object(offers, "_deal_admin_recipient_ids", return_value=[8001, 7001]),
            patch.object(
                deal_gate, "get_advert_offer_joined", return_value={"seq_in_advert": 9}
            ),
        ):
            await deal_gate._broadcast_buyer_receipt_confirmation(
                bot, offer_id=41, receipt_index=0, receipt=receipt
            )

        self.assertEqual(bot.send_message.await_count, 2)
        recipients = {call.args[0] for call in bot.send_message.await_args_list}
        self.assertEqual(recipients, {7001, 7002})
        self.assertIn("مبلغ دریافت‌شده", bot.send_message.await_args.args[1])

    def test_admin_buyer_section_lists_received_state_for_each_receipt(self):
        from handlers import offers

        receipts = [
            {
                "type": "photo",
                "accounting_status": "ready_for_review",
                "amount_rial": 40_000_000,
                "expected_rial": 100_000_000,
                "reviewer_received_at": 10,
            },
            {
                "type": "photo",
                "accounting_status": "ready_for_review",
                "amount_rial": 60_000_000,
                "expected_rial": 100_000_000,
            },
        ]
        gate = {
            "offer_id": 41,
            "buyer_telegram_id": 111,
            "buyer_toman_card_sent_at": 1,
        }
        with patch(
            "database.db.deal_gate_buyer_receipt_list", return_value=receipts
        ):
            body = offers._buyer_toman_receipt_admin_line_html(gate)

        self.assertIn("فیش 1", body)
        self.assertIn("فیش 2", body)
        self.assertIn("✅ دریافت تأیید شد", body)
        self.assertIn("⏳ دریافت تأیید نشده", body)

    def test_admin_mixed_receipt_slides_keep_original_numbers_and_pending_state(self):
        from handlers import offers

        receipts = [
            {
                "type": "photo",
                "accounting_status": "duplicate",
                "amount_rial": 10_000_000,
            },
            {
                "type": "photo",
                "accounting_status": "processing",
                "amount_rial": 0,
            },
            {
                "type": "text",
                "text": "رسید دوم معتبر",
                "accounting_status": "ready_for_review",
                "amount_rial": 50_000_000,
                "expected_rial": 100_000_000,
                "reviewer_received_at": 10,
            },
        ]
        gate = {
            "offer_id": 41,
            "buyer_telegram_id": 111,
            "buyer_toman_card_sent_at": 1,
        }
        with patch(
            "database.db.deal_gate_buyer_receipt_list", return_value=receipts
        ):
            body = offers._buyer_toman_receipt_admin_line_html(
                gate, slides_mode=True
            )

        self.assertIn("فیش 2", body)
        self.assertIn("مبلغ نامشخص", body)
        self.assertIn("⏳ دریافت تأیید نشده", body)
        self.assertIn("فیش 3", body)
        self.assertIn("✅ دریافت تأیید شد", body)
        self.assertIn("رسید دوم معتبر", body)


if __name__ == "__main__":
    unittest.main()
