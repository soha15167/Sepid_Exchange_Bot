"""A user's simultaneous deals must never share implicit input state."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _gate(offer_id: int, advert_id: int) -> dict:
    return {
        "offer_id": offer_id,
        "advert_rowid": advert_id,
        "buyer_telegram_id": 10,
        "seller_telegram_id": 20 + offer_id,
        "gate_status": "accounts",
        "buyer_accounts_text": "",
        "seller_accounts_text": "",
    }


class MultiDealAccountIsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_ambiguous_accounts_require_explicit_deal_selection(self):
        from handlers import deal_gate

        gates = [_gate(101, 4001), _gate(102, 4002)]
        context = SimpleNamespace(user_data={})
        with patch.object(
            deal_gate, "deal_gate_accounts_for_user", return_value=gates
        ):
            selected = deal_gate._resolve_party_accounts_gate(context, 10)

        self.assertIsNone(selected)
        self.assertTrue(
            context.user_data[deal_gate._DEAL_ACC_REQUIRE_PICK_KEY]
        )

        context.user_data[deal_gate._DEAL_ACC_OFFER_KEY] = 101
        with patch.object(
            deal_gate, "deal_gate_accounts_for_user", return_value=gates
        ):
            selected = deal_gate._resolve_party_accounts_gate(context, 10)
        self.assertEqual(selected["offer_id"], 101)

    def test_finishing_one_deal_does_not_clear_another_selected_deal(self):
        from handlers import deal_gate

        user_data = {deal_gate._DEAL_ACC_OFFER_KEY: 202}
        context = SimpleNamespace(
            user_data=user_data,
            application=SimpleNamespace(user_data={10: user_data}),
        )

        deal_gate._clear_party_accounts_offer(
            context, 10, offer_id=201
        )
        self.assertEqual(user_data[deal_gate._DEAL_ACC_OFFER_KEY], 202)

        deal_gate._clear_party_accounts_offer(
            context, 10, offer_id=202
        )
        self.assertNotIn(deal_gate._DEAL_ACC_OFFER_KEY, user_data)

    async def test_account_pick_arms_only_the_selected_deal(self):
        from handlers import deal_gate

        gates = [_gate(101, 4001), _gate(102, 4002)]
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=10),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={},
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)
        with (
            patch.object(
                deal_gate, "deal_gate_accounts_for_user", return_value=gates
            ),
            patch.object(
                deal_gate,
                "get_advert_offer_joined",
                return_value={"seq_in_advert": 2},
            ),
        ):
            await deal_gate._handle_account_deal_pick_callback(
                update, context, 102
            )

        self.assertEqual(
            context.user_data[deal_gate._DEAL_ACC_OFFER_KEY], 102
        )
        query.answer.assert_awaited_once_with("این معامله انتخاب شد.")
        sent = context.bot.send_message.await_args.kwargs
        self.assertEqual(sent["chat_id"], 10)
        self.assertIn("آگهی <b>4002</b>", sent["text"])
        self.assertIn("پیشنهاد <b>2</b>", sent["text"])
        self.assertIn("نقش شما: <b>خریدار یورو</b>", sent["text"])

    async def test_switching_receipt_deal_removes_old_prompt(self):
        from handlers import deal_gate

        context = SimpleNamespace(
            user_data={
                deal_gate._DEAL_RCPT_KEY: {
                    "offer_id": 101,
                    "party": "buyer",
                }
            },
            bot=object(),
        )
        with patch.object(
            deal_gate,
            "_purge_rcpt_prompt_msgs",
            new=AsyncMock(),
        ) as purge:
            already_active = await deal_gate._party_receipt_prepare_switch(
                context, 10, 102, "buyer"
            )

        self.assertFalse(already_active)
        self.assertNotIn(deal_gate._DEAL_RCPT_KEY, context.user_data)
        purge.assert_awaited_once_with(
            context.bot,
            deal_gate.user_data_store,
            10,
            101,
        )


class AdminDealIdentityHeaderTests(unittest.TestCase):
    def test_admin_header_is_persian_and_identifies_one_deal(self):
        from handlers.offers import _deal_admin_identity_header_html

        text = _deal_admin_identity_header_html(
            {"gate_status": "accounts"},
            offer_id=102,
            advert_id=4002,
            offer_sequence=3,
            advert_link_html='<a href="https://t.me/example/1">آگهی 4002</a>',
        )

        self.assertIn("مشخصات معامله", text)
        self.assertIn("آگهی <b>4002</b>", text)
        self.assertIn("پیشنهاد <b>3</b>", text)
        self.assertIn("کد معامله <code>102</code>", text)
        self.assertIn("نقش‌ها: <b>خریدار یورو / فروشنده یورو</b>", text)
        self.assertIn("مرحله فعلی: <b>دریافت اطلاعات حساب</b>", text)


if __name__ == "__main__":
    unittest.main()
