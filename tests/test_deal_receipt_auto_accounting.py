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
                self.deal_gate, "_buyer_expected_rial", return_value=expected_rial
            ),
            patch.object(
                self.deal_gate, "_buyer_dealer_name", return_value="nongb"
            ),
            patch.object(
                self.deal_gate, "sync_deal_admin_notification", new=AsyncMock()
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

    async def test_rial_text_receipt_is_submitted_with_deal_identity(self):
        text = """بلو
انتقال سجاد نجفی کوپس بین بانکی (پل)
سپرده: IR - ۲۱ ۰۵۶۰ ۶۱۱۸ ۲۸۰۰ ۵۱۴۷ ۹۶۸۲ ۰۱
مبلغ: ۳۰۹٬۵۷۵٬۰۰۰ ریال
تاریخ: ۲۰:۱۲"""
        post = await self._run(text, expected_rial=309_575_000)
        self.assertEqual(self.items[0]["accounting_status"], "submitted")
        self.assertEqual(self.items[0]["amount_rial"], 309_575_000)
        self.assertEqual(self.items[0]["bank_name"], "سامان")
        payload = post.call_args.kwargs["payload"]
        self.assertEqual(payload["iran_amount"], 309_575_000)
        self.assertEqual(payload["bank_name"], "سامان")
        self.assertEqual(payload["depositor_name"], "nongb")
        self.assertEqual(payload["description"], "آگهی 3453")
        self.assertEqual(payload["transfer_type"], "پل")

    async def test_explicit_toman_partial_receipt_is_converted_and_submitted(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۲۰٬۰۰۰٬۰۰۰ تومان\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertTrue(post.called)
        self.assertEqual(self.items[0]["amount_rial"], 200_000_000)
        self.assertEqual(self.items[0]["remaining_rial"], 300_000_000)

    async def test_receipt_that_exceeds_expected_total_waits_for_review(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۶۰۰٬۰۰۰٬۰۰۰ ریال\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "review")
        self.assertTrue(
            any("بیشتر" in warning for warning in self.items[0]["recognition_warnings"])
        )

    async def test_ambiguous_partial_currency_waits_for_review(self):
        post = await self._run(
            "بانک مقصد: ملی\nمبلغ: ۲۰٬۰۰۰٬۰۰۰\nتاریخ: ۱۴۰۵/۰۵/۰۱",
            expected_rial=500_000_000,
        )
        self.assertFalse(post.called)
        self.assertEqual(self.items[0]["accounting_status"], "review")


if __name__ == "__main__":
    unittest.main()
