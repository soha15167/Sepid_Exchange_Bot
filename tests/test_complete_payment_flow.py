"""Exercise payment transitions together against real SQLite, with external APIs faked."""

from __future__ import annotations

import gc
import itertools
import json
import sqlite3
import tempfile
import time
import unittest
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import settings
from database import db, outgoing_receipts, receipt_management
from handlers import deal_gate, deal_outgoing, offers


class CompletePaymentFlowTests(unittest.IsolatedAsyncioTestCase):
    OFFER_ID = 836
    ADVERT_ID = 3838
    BUYER_ID = 10
    SELLER_ID = 20
    REVIEWER_ID = 7001
    ADMIN_ID = 9001
    BUYER_RIAL = 1_457_250_000
    SELLER_RIAL = 1_442_750_000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(db, "DB_PATH", str(Path(self.temp.name) / "flow.db")))
        self.stack.enter_context(patch.object(deal_gate, "user_data_store", {}))
        for module in (deal_gate, deal_outgoing, settings):
            self.stack.enter_context(patch.object(module, "ADMIN_IDS", [self.ADMIN_ID]))
            self.stack.enter_context(patch.object(module, "DEAL_SUPPORT_ADMIN_IDS", []))
            self.stack.enter_context(patch.object(module, "DEAL_RECEIPT_REVIEWER_IDS", [self.REVIEWER_ID]))
        self.stack.enter_context(patch.object(offers, "ADMIN_IDS", [self.ADMIN_ID]))
        self.stack.enter_context(patch.object(offers, "ADMIN_NOTIFY_CHAT_IDS", []))
        self.post = self.stack.enter_context(patch(
            "utils.iran_panel_client.post_transaction", return_value=(True, "ok")
        ))
        self.mid = itertools.count(1000)
        self.bot = SimpleNamespace()
        for method in ("send_message", "send_photo", "send_document", "edit_message_text", "edit_message_caption"):
            setattr(self.bot, method, AsyncMock(side_effect=self._sent))
        self.bot.send_media_group = AsyncMock(side_effect=self._sent_album)
        self.bot.delete_message = AsyncMock(return_value=True)
        self.bot.edit_message_reply_markup = AsyncMock(return_value=True)
        self.bot.get_me = AsyncMock(return_value=SimpleNamespace(id=9999, username="offline_bot"))
        self.bot.get_chat = AsyncMock(return_value=SimpleNamespace(
            id=self.BUYER_ID, username="synthetic", first_name="Synthetic", last_name="User",
            full_name="Synthetic User", title="Synthetic", type="private",
        ))
        self.application = SimpleNamespace(
            bot=self.bot, user_data=defaultdict(dict), bot_data={}, job_queue=None,
        )
        db.ensure_schema()
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO users (telegram_id, full_name) VALUES (?, ?)",
                [(self.BUYER_ID, "Synthetic buyer"), (self.SELLER_ID, "Synthetic seller")],
            )
            conn.execute(
                "INSERT INTO euro_adverts (id, user_id, euro_amount, rate_toman, operation, status, "
                "channel_message_id, channel_chat_id) VALUES (?, ?, 500, 290000, 'فروش', 'فعال', 99, -100999)",
                (self.ADVERT_ID, self.SELLER_ID),
            )
            conn.execute(
                "INSERT INTO advert_offers (id, advert_rowid, proposer_telegram_id, rate_toman, "
                "proposed_euro_amount, created_at, status) VALUES (?, ?, ?, 290000, 500, ?, 'accepted')",
                (self.OFFER_ID, self.ADVERT_ID, self.BUYER_ID, str(int(time.time()))),
            )
        db.deal_gate_upsert(
            offer_id=self.OFFER_ID, advert_rowid=self.ADVERT_ID,
            buyer_telegram_id=self.BUYER_ID, seller_telegram_id=self.SELLER_ID,
            gate_status="completed", buyer_response="yes", seller_response="yes",
            buyer_accounts_text="SYNTHETIC EUR ACCOUNT",
            seller_accounts_text="SYNTHETIC TOMAN ACCOUNT", buyer_toman_card_sent_at=100,
        )
        db.deal_gate_append_buyer_receipt(
            self.OFFER_ID, entry_type="text", text="Synthetic receipt; bank details unreadable",
            source_message_id=500,
        )
        db.deal_gate_update_buyer_receipt(
            self.OFFER_ID, 0, amount_rial=self.BUYER_RIAL, accounting_status="review",
            bank_name="", transfer_type="", jdate="", reviewer_notify_mids={str(self.REVIEWER_ID): 501},
        )
        db.deal_gate_confirm_buyer_receipt_received(self.OFFER_ID, 0, self.REVIEWER_ID)
        deal_gate._buyer_toman_settlement_locks.clear()
        deal_gate._buyer_receipt_copy_locks.clear()
        deal_outgoing._preview_locks.clear()

    def tearDown(self):
        self.stack.close()
        deal_gate._buyer_toman_settlement_locks.clear()
        deal_gate._buyer_receipt_copy_locks.clear()
        deal_outgoing._preview_locks.clear()
        gc.collect()
        self.temp.cleanup()

    async def _sent(self, *args, **kwargs):
        return SimpleNamespace(message_id=next(self.mid), chat_id=kwargs.get("chat_id", args[0] if args else 0))

    async def _sent_album(self, *args, **kwargs):
        return [await self._sent(*args, **kwargs) for _ in kwargs.get("media", [])]

    def _context(self, uid):
        return SimpleNamespace(
            bot=self.bot, application=self.application, user_data=self.application.user_data[uid],
            bot_data=self.application.bot_data,
        )

    def _query(self, uid, data):
        message = SimpleNamespace(
            message_id=next(self.mid), chat_id=uid, text="", caption=None, photo=[], document=None,
            edit_reply_markup=AsyncMock(), edit_text=AsyncMock(),
        )
        return SimpleNamespace(callback_query=SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=uid), message=message, answer=AsyncMock(),
        ))

    def _upload(self, uid, text):
        async def reply(*args, **kwargs):
            return await self._sent(uid)

        return SimpleNamespace(
            effective_user=SimpleNamespace(id=uid),
            message=SimpleNamespace(
                text=text, caption=None, photo=[], document=None, message_id=next(self.mid), chat_id=uid,
                reply_text=AsyncMock(side_effect=reply),
            ),
        )

    def _payout_text(self):
        return (
            f"بانک: ملت\nمقصد: ملی\nنام گیرنده: فروشنده آزمایشی\nمبلغ: {self.SELLER_RIAL:,}"
            "\nنوع: پایا\nتاریخ: 1405/08/04"
        )

    async def _advance_to_viewer_receipt(self):
        gate = db.deal_gate_get(self.OFFER_ID)
        self.assertEqual(deal_gate._buyer_expected_rial(gate), self.BUYER_RIAL)
        self.assertEqual(deal_gate._seller_expected_rial(gate), self.SELLER_RIAL)
        reviewer = self._context(self.REVIEWER_ID)
        await deal_gate.deal_admin_toman_settled_callback(
            self._query(self.REVIEWER_ID, f"adm|tomset|{self.OFFER_ID}|0|{self.BUYER_RIAL}"), reviewer,
        )
        gate = db.deal_gate_get(self.OFFER_ID)
        self.assertGreater(int(gate["buyer_toman_settled_at"] or 0), 0)
        self.assertGreater(int(gate["seller_eur_account_sent_at"] or 0), 0)
        self.assertEqual(db.deal_gate_buyer_receipt_list(self.OFFER_ID)[0]["accounting_status"], "review")
        self.post.assert_not_called()

        seller = self._context(self.SELLER_ID)
        await deal_gate.deal_gate_callback(
            self._query(self.SELLER_ID, f"deal|srcpt|{self.OFFER_ID}|go"), seller,
        )
        self.assertTrue(await deal_gate._deal_receipt_try_message(
            self._upload(self.SELLER_ID, "SYNTHETIC EUR 500 TRANSFER RECEIPT"), seller,
        ))
        receipts = db.deal_gate_seller_receipt_list(self.OFFER_ID)
        self.assertEqual(len(receipts), 1)
        self.assertFalse(receipts[0]["buyer_confirmed_at"])
        data = deal_gate._buyer_euro_settled_keyboard(self.OFFER_ID, 0).inline_keyboard[0][0].callback_data
        await deal_gate.deal_gate_callback(self._query(self.BUYER_ID, data), self._context(self.BUYER_ID))
        gate = db.deal_gate_get(self.OFFER_ID)
        self.assertGreater(int(db.deal_gate_seller_receipt_list(self.OFFER_ID)[0]["buyer_confirmed_at"]), 0)
        self.assertGreater(int(gate["viewer_toman_due_at"]), 0)
        await deal_gate.run_viewer_toman_payment_sweep(self.bot, now=int(gate["viewer_toman_due_at"]) + 1)
        self.assertIn(str(self.REVIEWER_ID), json.loads(db.deal_gate_get(self.OFFER_ID)["viewer_toman_notify_mids"]))
        await deal_gate.deal_gate_callback(
            self._query(self.REVIEWER_ID, f"viewerpay|claim|{self.OFFER_ID}"), reviewer,
        )
        self.assertEqual(int(db.deal_gate_get(self.OFFER_ID)["viewer_toman_claimed_by"]), self.REVIEWER_ID)
        return reviewer, self._upload(self.REVIEWER_ID, self._payout_text())

    async def _assert_seller_can_close(self):
        await deal_gate.deal_gate_callback(
            self._query(self.SELLER_ID, f"deal|stomcfm|{self.OFFER_ID}"), self._context(self.SELLER_ID),
        )
        gate = db.deal_gate_get(self.OFFER_ID)
        self.assertEqual(gate["gate_status"], "closed")
        self.assertGreater(int(gate["seller_toman_settled_at"]), 0)
        with sqlite3.connect(db.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT status FROM advert_offers WHERE id=?", (self.OFFER_ID,)).fetchone()[0], "gate_closed")
            self.assertEqual(conn.execute("SELECT status FROM euro_adverts WHERE id=?", (self.ADVERT_ID,)).fetchone()[0], "بسته")

    async def test_confirmed_exact_payment_with_unreadable_details_completes_the_deal(self):
        reviewer, upload = await self._advance_to_viewer_receipt()
        self.assertTrue(await deal_gate._viewer_toman_receipt_try(upload, reviewer))
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [("submitted", 1)])
        self.post.assert_called_once()
        payload = self.post.call_args.kwargs["payload"]
        self.assertEqual(payload["iran_type"], "خروجی")
        self.assertEqual(payload["iran_amount"], self.SELLER_RIAL)
        self.assertEqual(payload["description"], f"آگهی {self.ADVERT_ID}")
        self.assertGreater(int(db.deal_gate_get(self.OFFER_ID)["seller_toman_close_enabled_at"]), 0)
        await self._assert_seller_can_close()

    async def test_admin_toman_button_advances_confirmed_screenshot_payment_before_bookkeeping(self):
        admin = self._context(self.ADMIN_ID)
        update = self._query(self.ADMIN_ID, f"adm|tomset|{self.OFFER_ID}")
        received_at = db.deal_gate_buyer_receipt_list(self.OFFER_ID)[0]["reviewer_received_at"]
        await deal_gate.deal_admin_toman_settled_callback(update, admin)
        self.assertFalse(db.deal_gate_get(self.OFFER_ID)["buyer_toman_settled_at"])
        markup = update.callback_query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
        update.callback_query.data = markup.inline_keyboard[0][0].callback_data
        self.assertEqual(update.callback_query.data, f"adm|tomset|{self.OFFER_ID}|yes")
        await deal_gate.deal_admin_toman_settled_callback(update, admin)
        gate = db.deal_gate_get(self.OFFER_ID)
        self.assertGreater(int(gate["buyer_toman_settled_at"] or 0), 0)
        self.assertGreater(int(gate["seller_eur_account_sent_at"] or 0), 0)
        receipt = db.deal_gate_buyer_receipt_list(self.OFFER_ID)[0]
        self.assertEqual(receipt["accounting_status"], "review")
        self.assertEqual(receipt["reviewer_received_at"], received_at)
        self.post.assert_not_called()
        outbound = db.bot_outbound_log_list(self.OFFER_ID)
        self.assertEqual(sum(row["tag"] == "حساب یوروی خریدار به فروشنده"
                             and int(row["recipient_telegram_id"]) == self.SELLER_ID for row in outbound), 1)
        self.assertEqual(sum(row["tag"] == "وضعیت: تومان نشست (خریدار)"
                             and int(row["recipient_telegram_id"]) == self.BUYER_ID for row in outbound), 1)
        await deal_gate.deal_admin_toman_settled_callback(update, admin)
        self.post.assert_not_called()
        self.assertEqual(len(db.bot_outbound_log_list(self.OFFER_ID)), len(outbound))

    async def test_unknown_outgoing_bookkeeping_does_not_block_seller_confirmation(self):
        reviewer, upload = await self._advance_to_viewer_receipt()
        self.post.return_value = (False, "outcome unknown")
        self.assertTrue(await deal_gate._viewer_toman_receipt_try(upload, reviewer))
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [("unknown", 1)])
        await self._assert_seller_can_close()
        await deal_gate.run_deal_delivery_retry_sweep(self.bot)
        self.post.assert_called_once()
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [("unknown", 1)])

    async def test_failed_payout_delivery_retries_before_seller_can_close(self):
        reviewer, upload = await self._advance_to_viewer_receipt()
        send = self.bot.send_message.side_effect

        async def fail_seller_receipt(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else 0)
            if chat_id == self.SELLER_ID and "فیش پرداخت تومان به فروشنده" in kwargs.get("text", ""):
                raise RuntimeError("synthetic Telegram outage")
            return await send(*args, **kwargs)

        self.bot.send_message.side_effect = fail_seller_receipt
        self.assertTrue(await deal_gate._viewer_toman_receipt_try(upload, reviewer))
        self.assertFalse(db.deal_gate_get(self.OFFER_ID)["seller_toman_close_enabled_at"])
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [("submitted", 1)])
        await deal_gate.deal_gate_callback(
            self._query(self.SELLER_ID, f"deal|stomcfm|{self.OFFER_ID}"), self._context(self.SELLER_ID),
        )
        self.assertEqual(db.deal_gate_get(self.OFFER_ID)["gate_status"], "completed")
        self.bot.send_message.side_effect = send
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("UPDATE deal_delivery_queue SET next_attempt_at=0 WHERE offer_id=?", (self.OFFER_ID,))
        self.assertEqual(await deal_gate.run_deal_delivery_retry_sweep(self.bot), 1)
        self.assertGreater(int(db.deal_gate_get(self.OFFER_ID)["seller_toman_close_enabled_at"]), 0)
        self.post.assert_called_once()
        await self._assert_seller_can_close()

    async def test_eur_receipt_notification_retries_once_with_current_confirmation_and_no_outbound_log(self):
        db.deal_gate_append_seller_receipt(self.OFFER_ID, entry_type="photo", file_id="synthetic-eur")
        self.bot.send_photo.side_effect = RuntimeError("synthetic Telegram outage")
        args = dict(offer_id=self.OFFER_ID, gate=db.deal_gate_get(self.OFFER_ID),
                    receipt_index=0, entry_type="photo", file_id="synthetic-eur")
        await deal_gate._notify_buyer_euro_receipt_confirm(self.bot, **args)
        queue = db.deal_delivery_list_for_offer(self.OFFER_ID)
        self.assertEqual([item["status"] for item in queue], ["failed"])
        self.assertEqual(db.bot_outbound_log_list(self.OFFER_ID), [])
        db.deal_gate_append_seller_receipt(self.OFFER_ID, entry_type="text", text="second EUR receipt")
        self.bot.send_photo.side_effect = self._sent
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("UPDATE deal_delivery_queue SET next_attempt_at=0")
        self.assertEqual(await deal_gate.run_deal_delivery_retry_sweep(self.bot), 1)
        markup = self.bot.send_photo.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,
                         deal_gate._buyer_euro_settled_keyboard(self.OFFER_ID, 0).inline_keyboard[0][0].callback_data)
        await deal_gate._notify_buyer_euro_receipt_confirm(self.bot, **args)
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertFalse(any(item["tag"] == "فیش یورو برای تأیید خریدار"
                             for item in db.bot_outbound_log_list(self.OFFER_ID)))

    async def test_eur_retry_retires_archived_confirmed_or_closed_receipts_without_sending(self):
        for change in ("archived", "confirmed", "closed"):
            with self.subTest(change=change):
                db.deal_gate_upsert(offer_id=self.OFFER_ID, advert_rowid=self.ADVERT_ID,
                    buyer_telegram_id=self.BUYER_ID, seller_telegram_id=self.SELLER_ID,
                    gate_status="completed", seller_receipt_log="[]")
                items = db.deal_gate_append_seller_receipt(self.OFFER_ID, entry_type="photo", file_id=change)
                self.bot.send_photo.side_effect = RuntimeError("synthetic Telegram outage")
                await deal_gate._notify_buyer_euro_receipt_confirm(self.bot, offer_id=self.OFFER_ID,
                    gate=db.deal_gate_get(self.OFFER_ID), receipt_index=0, entry_type="photo", file_id=change)
                if change == "archived":
                    self.assertTrue(receipt_management.archive(self.OFFER_ID, "euro", 0,
                        receipt_management.fingerprint(items[0]), self.ADMIN_ID))
                elif change == "confirmed":
                    self.assertTrue(db.deal_gate_confirm_seller_receipt_buyer(self.OFFER_ID, 0))
                else:
                    db.deal_gate_upsert(offer_id=self.OFFER_ID, advert_rowid=self.ADVERT_ID,
                        buyer_telegram_id=self.BUYER_ID, seller_telegram_id=self.SELLER_ID, gate_status="closed")
                self.bot.send_photo.reset_mock()
                self.bot.send_photo.side_effect = self._sent
                with sqlite3.connect(db.DB_PATH) as conn:
                    conn.execute("UPDATE deal_delivery_queue SET next_attempt_at=0")
                self.assertEqual(await deal_gate.run_deal_delivery_retry_sweep(self.bot), 0)
                self.bot.send_photo.assert_not_awaited()
                self.assertTrue(all(item["status"] == "sent" for item in db.deal_delivery_list_for_offer(self.OFFER_ID)))

    async def _assert_admin_queued_payout_is_booked(self, *, photo):
        admin = self._context(self.ADMIN_ID)
        admin.user_data[deal_gate._DEAL_ADMIN_STOM_KEY] = {
            "offer_id": self.OFFER_ID, "advert_rowid": self.ADVERT_ID,
        }
        upload = self._upload(self.ADMIN_ID, self._payout_text())
        if photo:
            upload.message.text = None
            upload.message.caption = self._payout_text()
            upload.message.photo = [SimpleNamespace(file_id="admin-payout", file_unique_id="unique-admin-payout")]
            self.bot.get_file = AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock()))
            from handlers.iran_panel_sync import _parse_payload_out
            payload, _ = _parse_payload_out(self._payout_text())
            self.stack.enter_context(patch("handlers.iran_panel_sync._read_receipt_image",
                new=AsyncMock(return_value=(payload, self._payout_text(), ""))))
        method = "send_photo" if photo else "send_message"
        async def fail_seller(*args, **kwargs):
            if kwargs.get("chat_id", args[0] if args else 0) == self.SELLER_ID:
                raise RuntimeError("synthetic Telegram outage")
            return await self._sent(*args, **kwargs)
        getattr(self.bot, method).side_effect = fail_seller
        handler = deal_gate._deal_admin_stom_try_photo if photo else deal_gate._deal_admin_stom_try_message
        self.assertTrue(await handler(upload, admin))
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [("submitted", 1)])
        self.assertEqual(db.deal_delivery_list_for_offer(self.OFFER_ID)[0]["status"], "failed")
        self.post.assert_called_once()
        getattr(self.bot, method).side_effect = self._sent
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("UPDATE deal_delivery_queue SET next_attempt_at=0")
        self.assertEqual(await deal_gate.run_deal_delivery_retry_sweep(self.bot), 1)
        self.post.assert_called_once()

    async def test_admin_text_payout_is_booked_when_seller_delivery_is_queued(self):
        await self._assert_admin_queued_payout_is_booked(photo=False)

    async def test_admin_photo_payout_is_booked_when_seller_delivery_is_queued(self):
        await self._assert_admin_queued_payout_is_booked(photo=True)

    async def test_closed_viewer_upload_session_cannot_forward_or_book_another_payout(self):
        reviewer = self._context(self.REVIEWER_ID)
        reviewer.user_data[deal_gate._VIEWER_TOMAN_RECEIPT_KEY] = {"offer_id": self.OFFER_ID}
        db.deal_gate_upsert(offer_id=self.OFFER_ID, advert_rowid=self.ADVERT_ID,
            buyer_telegram_id=self.BUYER_ID, seller_telegram_id=self.SELLER_ID,
            gate_status="closed", viewer_toman_claimed_by=self.REVIEWER_ID)
        self.assertTrue(await deal_gate._viewer_toman_receipt_try(
            self._upload(self.REVIEWER_ID, self._payout_text()), reviewer))
        self.assertNotIn(deal_gate._VIEWER_TOMAN_RECEIPT_KEY, reviewer.user_data)
        self.assertEqual(db.deal_delivery_list_for_offer(self.OFFER_ID), [])
        self.assertEqual(outgoing_receipts.summary(self.OFFER_ID), [])
        self.post.assert_not_called()

    def test_new_unconfirmed_eur_receipt_pauses_viewer_prompt_and_claim(self):
        db.deal_gate_append_seller_receipt(self.OFFER_ID, entry_type="text", text="first EUR receipt")
        self.assertTrue(db.deal_gate_confirm_seller_receipt_buyer(self.OFFER_ID, 0))
        db.deal_gate_upsert(offer_id=self.OFFER_ID, advert_rowid=self.ADVERT_ID,
            buyer_telegram_id=self.BUYER_ID, seller_telegram_id=self.SELLER_ID, viewer_toman_due_at=1)
        self.assertEqual(len(db.deal_gate_list_due_viewer_toman_payments()), 1)
        db.deal_gate_append_seller_receipt(self.OFFER_ID, entry_type="text", text="new EUR receipt")
        self.assertEqual(db.deal_gate_list_due_viewer_toman_payments(), [])
        self.assertFalse(db.deal_gate_claim_viewer_toman_payment(self.OFFER_ID, self.REVIEWER_ID))
        self.assertTrue(db.deal_gate_confirm_seller_receipt_buyer(self.OFFER_ID, 1))
        self.assertEqual(len(db.deal_gate_list_due_viewer_toman_payments()), 1)
        self.assertTrue(db.deal_gate_claim_viewer_toman_payment(self.OFFER_ID, self.REVIEWER_ID))
