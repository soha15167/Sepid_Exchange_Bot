"""Automatic deal-bound Iran-panel accounting for buyer Toman receipts."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class BuyerReceiptAutoAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handlers import deal_gate

        self.deal_gate = deal_gate
        self.gate = {
            "offer_id": 258,
            "advert_rowid": 3453,
            "buyer_telegram_id": 1462958044,
        }
        self.items = [
            {
                "type": "text",
                "text": "",
                "accounting_status": "pending",
                "source_message_id": 10,
            }
        ]

    def _update(self, _offer_id, index, **fields):
        self.items[index].update(fields)
        return dict(self.items[index])

    async def _run(self, text: str, *, expected_rial: int):
        post = Mock(return_value=(True, "ok"))
        with (
            patch.object(
                self.deal_gate,
                "deal_gate_buyer_receipt_list",
                side_effect=lambda _oid: self.items,
            ),
            patch.object(
                self.deal_gate,
                "deal_gate_update_buyer_receipt",
                side_effect=self._update,
            ),
            patch.object(
                self.deal_gate,
                "deal_gate_has_submitted_buyer_receipt",
                return_value=False,
            ),
            patch.object(
                self.deal_gate, "_buyer_expected_rial", return_value=expected_rial
            ),
            patch.object(
                self.deal_gate, "_buyer_dealer_name", return_value="nongb"
            ),
            patch.object(
                self.deal_gate, "sync_deal_admin_notification", new=AsyncMock()
            ),
            patch.object(
                self.deal_gate, "_send_deal_receipt_review_preview", new=AsyncMock()
            ),
            patch("utils.iran_panel_client.post_transaction", post),
        ):
            await self.deal_gate._auto_account_buyer_receipt(
                SimpleNamespace(bot=SimpleNamespace()),
                gate=self.gate,
                receipt_index=0,
                entry_type="text",
                text=text,
            )
        return post

    async def test_rial_text_receipt_waits_for_admin_review(self):
        text = """بلو
انتقال سجاد نجفی کوپس بین بانکی (پل)
سپرده: IR - ۲۱ ۰۵۶۰ ۶۱۱۸ ۲۸۰۰ ۵۱۴۷ ۹۶۸۲ ۰۱
مبلغ: ۳۰۹٬۵۷۵٬۰۰۰ ریال
تاریخ: ۲۰:۱۲"""
        post = await self._run(text, expected_rial=309_575_000)
        self.assertEqual(self.items[0]["accounting_status"], "ready_for_review")
        self.assertEqual(self.items[0]["amount_rial"], 309_575_000)
        self.assertEqual(self.items[0]["bank_name"], "سامان")
        self.assertFalse(post.called)

    async def test_explicit_toman_partial_receipt_is_converted_for_review(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۲۰٬۰۰۰٬۰۰۰ تومان\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "ready_for_review")
        self.assertEqual(self.items[0]["amount_rial"], 200_000_000)
        self.assertEqual(self.items[0]["remaining_rial"], 300_000_000)

    async def test_receipt_that_exceeds_expected_total_waits_for_review(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۶۰۰٬۰۰۰٬۰۰۰ ریال\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "ready_for_review")
        self.assertTrue(
            any("بیشتر" in warning for warning in self.items[0]["recognition_warnings"])
        )

    async def test_outgoing_receipt_with_small_bank_fee_is_prepared_as_incoming(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۱۰۰٬۵۰۰٬۰۰۰ ریال\n"
            "تاریخ: ۱۴۰۵/۰۵/۰۱\nبرداشت از حساب",
            expected_rial=100_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "ready_for_review")
        self.assertEqual(self.items[0]["amount_rial"], 100_000_000)
        self.assertEqual(self.items[0]["recognized_amount_rial"], 100_500_000)
        self.assertTrue(self.items[0]["fee_adjusted_to_remaining"])

    async def test_ambiguous_partial_currency_waits_for_review(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۲۰٬۰۰۰٬۰۰۰\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "ready_for_review")

    def test_buyer_outgoing_direction_is_not_a_deal_accounting_warning(self):
        warnings = self.deal_gate._deal_bound_receipt_warnings(
            {
                "_detected_direction": "out",
                "_recognition_warnings": [
                    "جهت روی رسید «خروجی» است ولی دستور «ورودی» انتخاب شده"
                ],
            }
        )
        self.assertEqual(warnings, [])

    def test_small_outgoing_bank_fee_is_removed_from_incoming_amount(self):
        amount, adjusted = self.deal_gate._deal_bound_receipt_amount(
            100_500_000,
            100_000_000,
            detected_direction="out",
        )
        self.assertTrue(adjusted)
        self.assertEqual(amount, 100_000_000)

    def test_large_amount_mismatch_is_never_adjusted(self):
        amount, adjusted = self.deal_gate._deal_bound_receipt_amount(
            102_000_000,
            100_000_000,
            detected_direction="out",
        )
        self.assertFalse(adjusted)
        self.assertEqual(amount, 102_000_000)

    async def test_admin_approval_posts_once_and_marks_submitted(self):
        self.items[0].update(
            accounting_status="ready_for_review", amount_rial=100_000_000,
            bank_name="ملی", transfer_type="پایا", jdate="1405/05/01",
        )
        query = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=SimpleNamespace())
        post = Mock(return_value=(True, "ok"))
        with (
            patch.object(self.deal_gate, "_require_full_deal_admin", new=AsyncMock(return_value=True)),
            patch.object(self.deal_gate, "deal_gate_get", return_value=self.gate),
            patch.object(self.deal_gate, "deal_gate_claim_buyer_receipt_submission", side_effect=[dict(self.items[0]), None]),
            patch.object(self.deal_gate, "deal_gate_update_buyer_receipt", side_effect=self._update),
            patch.object(self.deal_gate, "_buyer_dealer_name", return_value="nongb"),
            patch.object(self.deal_gate, "sync_deal_admin_notification", new=AsyncMock()),
            patch("utils.iran_panel_client.post_transaction", post),
        ):
            await self.deal_gate._handle_admin_receipt_review(
                update, context, offer_id=258, receipt_index=0, approve=True
            )
            await self.deal_gate._handle_admin_receipt_review(
                update, context, offer_id=258, receipt_index=0, approve=True
            )
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.items[0]["accounting_status"], "submitted")

    async def test_admin_rejection_never_posts(self):
        self.items[0]["accounting_status"] = "ready_for_review"
        query = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        with (
            patch.object(self.deal_gate, "_require_full_deal_admin", new=AsyncMock(return_value=True)),
            patch.object(self.deal_gate, "deal_gate_get", return_value=self.gate),
            patch.object(
                self.deal_gate,
                "deal_gate_reject_buyer_receipt",
                side_effect=lambda _oid, index: self._update(
                    _oid, index, accounting_status="rejected", panel_error=""
                ),
            ),
            patch.object(self.deal_gate, "sync_deal_admin_notification", new=AsyncMock()),
            patch("utils.iran_panel_client.post_transaction") as post,
        ):
            await self.deal_gate._handle_admin_receipt_review(
                SimpleNamespace(callback_query=query), SimpleNamespace(bot=SimpleNamespace()),
                offer_id=258, receipt_index=0, approve=False,
            )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "rejected")

    async def test_preview_shows_receipt_amount_and_separate_submit_amount(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        receipt = {
            "recognized_amount_rial": 100_500_000,
            "amount_rial": 100_000_000,
            "bank_name": "ملی",
            "transfer_type": "پایا",
            "jdate": "1405/05/01",
            "recognition_warnings": [],
        }
        with (
            patch.object(self.deal_gate, "ADMIN_IDS", [1]),
            patch.object(self.deal_gate, "_buyer_dealer_name", return_value="buyer"),
        ):
            await self.deal_gate._send_deal_receipt_review_preview(
                bot, gate=self.gate, receipt_index=0, receipt=receipt
            )
        sent = bot.send_message.await_args.kwargs
        self.assertIn("100,500,000", sent["text"])
        self.assertIn("100,000,000", sent["text"])
        callback_data = [
            button.callback_data
            for row in sent["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(callback_data, ["adm|rcptok|258|0", "adm|rcptno|258|0"])


if __name__ == "__main__":
    unittest.main()
