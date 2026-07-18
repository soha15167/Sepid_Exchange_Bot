"""Public channel deal status must follow settlement, not offer acceptance."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class ChannelDealStatusTests(unittest.TestCase):
    def test_each_live_gate_stage_has_an_accurate_persian_status(self):
        from handlers.offers import _channel_selected_offer_status_html

        cases = (
            ({"gate_status": "pending"}, "در انتظار تأیید نهایی طرفین"),
            ({"gate_status": "accounts"}, "اطلاعات حساب طرفین در حال ثبت است"),
            ({"gate_status": "completed"}, "پرداخت و تسویه هنوز تکمیل نشده است"),
            ({"gate_status": "rejected"}, "منتظر تصمیم ادمین است"),
            ({"gate_status": "closed"}, "این آگهی بسته شده است"),
            ({"offer_status": "accepted"}, "معامله هنوز تکمیل نشده است"),
        )
        for row, expected in cases:
            with self.subTest(row=row):
                text = _channel_selected_offer_status_html([row])
                self.assertIn(expected, text)
                self.assertNotIn("✅ این آگهی و معامله تکمیل شده است", text)

    def test_only_settled_closed_deal_is_labeled_completed(self):
        from handlers.offers import _channel_selected_offer_status_html

        text = _channel_selected_offer_status_html(
            [{"gate_status": "closed", "seller_toman_settled_at": 123}]
        )
        self.assertIn("✅ این آگهی و معامله تکمیل شده است", text)

    def test_selected_offer_icon_matches_real_stage(self):
        from handlers.offers import _channel_selected_offer_line_status

        self.assertEqual(
            _channel_selected_offer_line_status({"gate_status": "accounts"}),
            "selected_active",
        )
        self.assertEqual(
            _channel_selected_offer_line_status({"gate_status": "rejected"}),
            "selected_rejected",
        )
        self.assertEqual(
            _channel_selected_offer_line_status({"gate_status": "closed"}),
            "selected_closed",
        )
        self.assertEqual(
            _channel_selected_offer_line_status(
                {"gate_status": "closed", "seller_toman_settled_at": 123}
            ),
            "selected_completed",
        )

    def test_channel_block_no_longer_calls_accepted_offer_completed(self):
        from handlers import offers

        selected = {
            "id": 242,
            "rate_toman": 205000,
            "description": "",
            "proposer_telegram_id": 20,
            "seq_in_advert": 1,
            "offer_alias_name": "N.H",
            "proposed_euro_amount": 0,
            "offer_status": "accepted",
            "gate_status": "accounts",
            "seller_toman_settled_at": 0,
        }
        advert = {"operation": "فروش", "euro_amount": 500}
        with (
            patch.object(offers, "get_euro_advert_by_rowid", return_value=advert),
            patch.object(offers, "list_pending_offers_for_advert", return_value=[]),
            patch.object(offers, "list_rejected_offers_for_advert", return_value=[]),
            patch.object(
                offers,
                "list_accepted_offers_for_advert",
                return_value=[selected],
            ),
        ):
            html = offers.append_offer_lists_to_channel_html(
                "متن آگهی\n\n🤖 <b>ربات:</b> @Sepid_Group_Bot",
                3445,
            )

        self.assertIn("⏳ معامله در حال انجام است", html)
        self.assertIn("اطلاعات حساب طرفین در حال ثبت است", html)
        self.assertNotIn("✅ این آگهی تکمیل شده است", html)


class ChannelRefreshResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_channel_edit_returns_true(self):
        from handlers import offers

        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="Sepid_Group_Bot")),
            edit_message_text=AsyncMock(),
        )
        advert = {
            "rowid": 3445,
            "channel_chat_id": -100123,
            "channel_message_id": 3167,
        }
        with (
            patch.object(offers, "get_euro_advert_by_rowid", return_value=advert),
            patch("handlers.admin._build_channel_ad_text", return_value="base"),
            patch.object(
                offers,
                "append_offer_lists_to_channel_html",
                return_value="updated",
            ),
            patch.object(offers, "_inject_channel_bot_maintenance", side_effect=lambda x: x),
            patch.object(offers, "channel_ad_reply_markup", return_value=None),
        ):
            ok = await offers.refresh_advert_channel_post(bot, 3445)

        self.assertTrue(ok)
        bot.edit_message_text.assert_awaited_once()

    async def test_unexpected_channel_edit_failure_returns_false(self):
        from handlers import offers

        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="Sepid_Group_Bot")),
            edit_message_text=AsyncMock(side_effect=RuntimeError("synthetic failure")),
        )
        advert = {
            "rowid": 3445,
            "channel_chat_id": -100123,
            "channel_message_id": 3167,
        }
        with (
            patch.object(offers, "get_euro_advert_by_rowid", return_value=advert),
            patch("handlers.admin._build_channel_ad_text", return_value="base"),
            patch.object(
                offers,
                "append_offer_lists_to_channel_html",
                return_value="updated",
            ),
            patch.object(offers, "_inject_channel_bot_maintenance", side_effect=lambda x: x),
            patch.object(offers, "channel_ad_reply_markup", return_value=None),
        ):
            ok = await offers.refresh_advert_channel_post(bot, 3445)

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
