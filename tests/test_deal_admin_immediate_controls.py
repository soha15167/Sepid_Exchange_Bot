"""Admin deal controls must be available from the first accepted offer."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class TestDealAdminImmediateControls(unittest.IsolatedAsyncioTestCase):
    def test_pending_keyboard_has_persian_yes_and_no_for_both_parties(self):
        from handlers.deal_gate import deal_admin_party_proxy_rows

        rows = deal_admin_party_proxy_rows(
            41,
            {
                "gate_status": "pending",
                "buyer_response": "",
                "seller_response": "",
            },
        )
        buttons = [button for row in rows for button in row]
        callbacks = {button.callback_data for button in buttons}
        labels = {button.text for button in buttons}

        self.assertEqual(
            callbacks,
            {
                "adm|pxy|41|byes",
                "adm|pxy|41|bno",
                "adm|pxy|41|syes",
                "adm|pxy|41|sno",
            },
        )
        self.assertEqual(
            labels,
            {
                "✅ تأیید خریدار",
                "❌ رد خریدار",
                "✅ تأیید فروشنده",
                "❌ رد فروشنده",
            },
        )

    def test_confirmed_party_buttons_are_removed(self):
        from handlers.deal_gate import deal_admin_party_proxy_rows

        rows = deal_admin_party_proxy_rows(
            42,
            {
                "gate_status": "pending",
                "buyer_response": "yes",
                "seller_response": "",
            },
        )
        callbacks = {
            button.callback_data for row in rows for button in row
        }
        self.assertNotIn("adm|pxy|42|byes", callbacks)
        self.assertNotIn("adm|pxy|42|bno", callbacks)
        self.assertIn("adm|pxy|42|syes", callbacks)
        self.assertIn("adm|pxy|42|sno", callbacks)

    def test_pending_admin_banner_explains_proxy_controls_in_persian(self):
        from handlers.offers import _deal_admin_status_banner_html

        text = _deal_admin_status_banner_html(
            {
                "gate_status": "pending",
                "buyer_response": "",
                "seller_response": "yes",
            }
        )
        self.assertIn("منتظر تأیید نهایی خریدار", text)
        self.assertIn("ادمین می‌تواند", text)
        self.assertIn("تأیید یا رد کند", text)

    async def test_start_sends_admin_copy_without_waiting_for_party_answers(self):
        from handlers import deal_gate

        bot = object()
        context = SimpleNamespace(bot=bot)
        row = {"advert_rowid": 77, "seq_in_advert": 3}
        advert = {"operation": "فروش"}

        with (
            patch(
                "handlers.offers._offer_buyer_seller_telegram_ids",
                return_value=(111, 222),
            ),
            patch.object(deal_gate, "deal_gate_upsert") as upsert,
            patch.object(deal_gate, "_send_gate_messages", new=AsyncMock()) as send_gate,
            patch.object(deal_gate, "_schedule_gate_jobs") as schedule,
            patch.object(
                deal_gate,
                "sync_deal_admin_notification",
                new=AsyncMock(),
            ) as sync_admin,
            patch.object(
                deal_gate,
                "_refresh_deal_channel_status",
                new=AsyncMock(),
            ) as refresh_channel,
            patch.object(deal_gate, "_log"),
        ):
            await deal_gate.start_deal_final_gate(
                context,
                offer_id=41,
                row=row,
                advert=advert,
            )

        upsert.assert_called_once_with(
            offer_id=41,
            advert_rowid=77,
            buyer_telegram_id=111,
            seller_telegram_id=222,
            gate_status="pending",
        )
        send_gate.assert_awaited_once()
        schedule.assert_called_once_with(context, 41)
        sync_admin.assert_awaited_once_with(bot, 41)
        refresh_channel.assert_awaited_once_with(
            context,
            77,
            offer_id=41,
            gate_status="pending",
        )

    async def test_pending_sync_uses_acceptance_text_and_proxy_keyboard(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 77,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "pending",
            "buyer_response": "",
            "seller_response": "",
            "admin_notify_mids": "{}",
            "admin_notify_photo_mids": "{}",
        }
        row = {
            "id": 41,
            "advert_rowid": 77,
            "seq_in_advert": 3,
            "rate_toman": 90000,
            "owner_id": 111,
            "proposer_telegram_id": 222,
        }
        advert = {"operation": "فروش", "euro_amount": 100}
        bot = object()

        with (
            patch.object(deal_gate, "deal_gate_get", return_value=gate),
            patch.object(deal_gate, "get_advert_offer_joined", return_value=row),
            patch.object(deal_gate, "get_euro_advert_by_rowid", return_value=advert),
            patch(
                "handlers.offers._deal_admin_recipient_ids",
                return_value=[999],
            ),
            patch(
                "handlers.offers._post_acceptance_admin_message_html",
                return_value="اعلان آزمایشی",
            ) as build_html,
            patch.object(
                deal_gate,
                "_purge_legacy_admin_photo_replies",
                new=AsyncMock(),
            ),
            patch.object(
                deal_gate,
                "_edit_or_send_admin_notification",
                new=AsyncMock(return_value=321),
            ) as send_admin,
            patch.object(deal_gate, "deal_gate_upsert"),
        ):
            await deal_gate._sync_deal_admin_notification_locked(bot, 41)

        self.assertFalse(build_html.call_args.kwargs["accounts_status_mode"])
        self.assertFalse(build_html.call_args.kwargs["deal_complete"])
        markup = send_admin.call_args.kwargs["reply_markup"]
        callbacks = {
            button.callback_data
            for keyboard_row in markup.inline_keyboard
            for button in keyboard_row
        }
        self.assertTrue(
            {
                "adm|pxy|41|byes",
                "adm|pxy|41|bno",
                "adm|pxy|41|syes",
                "adm|pxy|41|sno",
            }.issubset(callbacks)
        )

    async def test_admin_can_refuse_on_behalf_of_buyer(self):
        from handlers import deal_gate

        gate = {
            "offer_id": 41,
            "advert_rowid": 77,
            "buyer_telegram_id": 111,
            "seller_telegram_id": 222,
            "gate_status": "pending",
            "buyer_response": "",
            "seller_response": "",
        }
        context = SimpleNamespace(bot=object())
        query = SimpleNamespace(answer=AsyncMock())

        with (
            patch.object(deal_gate, "deal_gate_get", return_value=gate),
            patch.object(deal_gate, "deal_gate_upsert") as upsert,
            patch.object(deal_gate, "_log"),
            patch.object(
                deal_gate,
                "_on_gate_rejected",
                new=AsyncMock(),
            ) as reject,
        ):
            await deal_gate._admin_proxy_party_final_no(
                context, 41, "buyer", query
            )

        saved = upsert.call_args.kwargs
        self.assertEqual(saved["buyer_response"], "no")
        self.assertGreater(saved["buyer_confirmed_at"], 0)
        query.answer.assert_awaited_once_with("❌ رد خریدار ثبت شد")
        reject.assert_awaited_once_with(
            context,
            41,
            rejector_id=111,
            party="خریدار",
            acted_by_admin=True,
        )


class TestAdminCommandMenu(unittest.IsolatedAsyncioTestCase):
    async def test_negotiation_report_command_is_visible_to_admins(self):
        import main

        bot = SimpleNamespace(set_my_commands=AsyncMock())
        with patch.object(main, "ADMIN_IDS", [5809748588]):
            await main._set_bot_command_menus(bot)

        admin_commands = bot.set_my_commands.await_args_list[1].args[0]
        command_names = {item.command for item in admin_commands}
        self.assertIn("neg_ad", command_names)


if __name__ == "__main__":
    unittest.main()
