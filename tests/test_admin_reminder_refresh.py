"""Reminder refreshes reuse the full deal card and preserve failed deliveries."""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from handlers import deal_gate as flow


class AdminReminderRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.gate = {
            "offer_id": 252, "advert_rowid": 3448,
            "buyer_telegram_id": 111, "seller_telegram_id": 222,
            "gate_status": "completed",
            "admin_notify_mids": json.dumps({"7001": 501, "7002": 502}),
            "admin_notify_photo_mids": json.dumps({
                "7001": {"album": [601], "fids": ["old-a"], "mode": "media_group"},
                "7002": {"album": [602], "fids": ["old-b"], "mode": "media_group"},
            }),
        }
        self.events = []
        self.bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=self._send),
            delete_message=AsyncMock(side_effect=self._delete),
            send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=9101)]),
        )
        self.stack.enter_context(patch.object(flow, "deal_gate_get", return_value=self.gate))
        self.stack.enter_context(patch.object(flow, "get_advert_offer_joined", return_value={"advert_rowid": 3448, "seq_in_advert": 2}))
        self.stack.enter_context(patch.object(flow, "get_euro_advert_by_rowid", return_value={"euro_amount": 100}))
        self.stack.enter_context(patch("handlers.offers._deal_admin_recipient_ids", return_value=[7001, 7002]))
        self.body = self.stack.enter_context(patch("handlers.offers._post_acceptance_admin_message_html", return_value="original full deal card"))
        self.stack.enter_context(patch("database.outgoing_receipts.summary", return_value=[]))
        self.slides = self.stack.enter_context(patch.object(flow, "_admin_deal_slides_plan", return_value=[]))
        self.stack.enter_context(patch.object(flow, "_admin_account_slides_plan", return_value=[]))
        self.keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("deal action", callback_data="adm|pay|252")]])
        self.build_keyboard = self.stack.enter_context(patch.object(flow, "deal_admin_main_keyboard", return_value=self.keyboard))
        self.save = self.stack.enter_context(patch.object(flow, "deal_gate_upsert"))
        flow._admin_sync_locks.clear()

    def tearDown(self):
        self.stack.close()
        flow._admin_sync_locks.clear()

    async def _send(self, chat_id, text, **kwargs):
        self.events.append(("send", chat_id))
        return SimpleNamespace(message_id=9000 + (chat_id - 7000))

    async def _delete(self, chat_id, message_id):
        self.events.append(("delete", message_id))

    async def test_refresh_resends_full_card_before_removing_previous_card_and_album(self):
        delivered = await flow.sync_deal_admin_notification(
            self.bot, 252, deal_complete=True, recipient_ids=[7001],
        )
        self.assertEqual(delivered, {7001: 9001})
        self.assertEqual(self.events[0], ("send", 7001))
        self.assertEqual(set(self.events[1:]), {("delete", 501), ("delete", 601)})
        self.assertEqual(self.bot.send_message.await_args.args, (7001, "original full deal card"))
        self.assertIs(self.bot.send_message.await_args.kwargs["reply_markup"], self.keyboard)
        mids = json.loads(self.save.call_args.kwargs["admin_notify_mids"])
        self.assertEqual(mids, {"7001": 9001, "7002": 502})
        photos = json.loads(self.save.call_args.kwargs["admin_notify_photo_mids"])
        self.assertEqual(photos["7002"]["album"], [602])
        self.body.assert_called_once()
        self.assertTrue(self.body.call_args.kwargs["deal_complete"])

    async def test_failed_send_keeps_old_card_album_and_stored_ids(self):
        self.bot.send_message.side_effect = TelegramError("offline")
        delivered = await flow.sync_deal_admin_notification(
            self.bot, 252, deal_complete=True, recipient_ids=[7001],
        )
        self.assertEqual(delivered, {})
        self.bot.delete_message.assert_not_awaited()
        self.save.assert_not_called()

    async def test_failed_recipient_does_not_block_another_admin(self):
        self.bot.send_message.side_effect = [RuntimeError("offline"), SimpleNamespace(message_id=9002)]
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, recipient_ids=[7001, 7002])
        self.assertEqual(delivered, {7002: 9002})
        deleted = {call.args[1] for call in self.bot.delete_message.await_args_list}
        self.assertEqual(deleted, {502, 602})
        self.assertEqual(json.loads(self.save.call_args.kwargs["admin_notify_mids"]), {"7001": 501, "7002": 9002})
        photos = json.loads(self.save.call_args.kwargs["admin_notify_photo_mids"])
        self.assertEqual(photos["7001"]["album"], [601])

    async def test_album_refresh_keeps_new_receipts_and_other_admin_metadata(self):
        self.slides.return_value = [("new-receipt", "receipt caption")]
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, deal_complete=True, recipient_ids=[7001])
        self.assertEqual(delivered, {7001: 9001})
        self.bot.send_media_group.assert_awaited_once()
        photos = json.loads(self.save.call_args.kwargs["admin_notify_photo_mids"])
        self.assertEqual(photos["7001"]["album"], [9101])
        self.assertEqual(photos["7002"]["album"], [602])
        self.assertNotIn(("delete", 9001), self.events)
        self.assertNotIn(("delete", 9101), self.events)

    async def test_failed_album_text_does_not_delete_existing_receipts(self):
        self.slides.return_value = [("new-receipt", "receipt caption")]
        self.bot.send_message.side_effect = TelegramError("offline")
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, deal_complete=True, recipient_ids=[7001])
        self.assertEqual(delivered, {})
        self.bot.send_media_group.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()
        self.save.assert_not_called()

    async def test_failed_media_delivery_rolls_back_new_card_and_retains_previous_card(self):
        self.slides.return_value = [("new-receipt", "receipt caption")]
        self.bot.send_media_group.side_effect = TelegramError("offline")
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, deal_complete=True, recipient_ids=[7001])
        self.assertEqual(delivered, {})
        self.assertEqual(self.events, [("send", 7001), ("delete", 9001)])
        self.save.assert_not_called()

    async def test_partial_album_delivery_removes_only_new_partial_replacement(self):
        self.slides.return_value = [("new-photo", "photo caption"), ("new-document", "document caption", "document")]
        self.bot.send_document = AsyncMock(side_effect=TelegramError("offline"))
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, deal_complete=True, recipient_ids=[7001])
        self.assertEqual(delivered, {})
        self.assertEqual(self.events[0], ("send", 7001))
        self.assertEqual(set(self.events[1:]), {("delete", 9001), ("delete", 9101)})
        self.save.assert_not_called()

    async def test_requested_recipient_must_be_a_configured_admin(self):
        delivered = await flow.sync_deal_admin_notification(self.bot, 252, recipient_ids=[123])
        self.assertEqual(delivered, {})
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_pending_and_accounts_deals_keep_their_current_stage(self):
        for status in ("pending", "accounts"):
            self.gate["gate_status"] = status
            await flow.sync_deal_admin_notification(self.bot, 252, recipient_ids=[7001])
            self.assertFalse(self.body.call_args.kwargs["deal_complete"])
            self.assertEqual(self.body.call_args.kwargs["accounts_status_mode"], status == "accounts")
            self.assertFalse(self.build_keyboard.call_args.kwargs["include_payment"])

    async def test_reminder_uses_fresh_completed_stage_instead_of_queued_snapshot(self):
        await flow.sync_deal_admin_notification(
            self.bot, 252, deal_complete=False, recipient_ids=[7001], reminder_only=True,
        )
        self.assertTrue(self.body.call_args.kwargs["deal_complete"])
        self.assertTrue(self.build_keyboard.call_args.kwargs["include_payment"])

    async def test_reminder_stops_if_deal_closed_or_receipt_arrived_while_waiting(self):
        for changes in ({"gate_status": "closed"}, {"gate_status": "completed", "seller_toman_close_enabled_at": 100}):
            self.gate.update(changes)
            delivered = await flow.sync_deal_admin_notification(self.bot, 252, recipient_ids=[7001], reminder_only=True)
            self.assertEqual(delivered, {})
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
