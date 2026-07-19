"""Deal messages keep Toman amounts concise, together, and copyable."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch


class DealMessageWordingTests(unittest.TestCase):
    def assert_clean_toman_wording(self, html: str) -> None:
        plain = re.sub(r"<[^>]+>", "", html)
        self.assertNotRegex(plain, r"تومان\s+تومان")
        self.assertNotRegex(html, r"</code>\s*تومان")

    def test_financial_blocks_make_rate_fee_and_final_amount_copyable(self):
        from handlers.offers import _financial_blocks_html

        owner, proposer = _financial_blocks_html(
            {"operation": "فروش"},
            195_000,
            100,
        )

        for text in (owner, proposer):
            self.assert_clean_toman_wording(text)
            self.assertIn("<code>195,000 تومان</code>", text)
            self.assertIn("<code>487,500 تومان</code>", text)
        self.assertIn("<code>19,012,500 تومان</code>", owner)
        self.assertIn("<code>19,987,500 تومان</code>", proposer)

    def test_generic_financial_block_makes_base_amount_copyable(self):
        from handlers.offers import _financial_blocks_html

        owner, _ = _financial_blocks_html(
            {"operation": "خدمات"},
            195_000,
            100,
        )

        self.assert_clean_toman_wording(owner)
        self.assertIn("جمع پایه: <code>19,500,000 تومان</code>", owner)

    def test_acceptance_summary_has_copyable_rate_fee_and_final_amount(self):
        from handlers.offers import _financial_accept_summary_html

        text = _financial_accept_summary_html(
            {"operation": "فروش"},
            195_000,
            100,
            owner_view=True,
        )

        self.assert_clean_toman_wording(text)
        self.assertIn("<code>195,000 تومان</code>", text)
        self.assertIn("<code>487,500 تومان</code>", text)
        self.assertIn("<code>19,012,500 تومان</code>", text)

    def test_deposit_instruction_contains_exactly_one_copyable_unit(self):
        from handlers.deal_gate import _buyer_toman_deposit_message_html

        text = _buyer_toman_deposit_message_html(
            advert_id=3448,
            offer_sequence=2,
            euro_amount=100,
            toman_amount=19_987_500,
            card_html="<code>6037 0000 0000 0000</code>",
        )

        self.assert_clean_toman_wording(text)
        self.assertEqual(text.count("<code>19,987,500 تومان</code>"), 1)
        self.assertIn("لطفاً مبلغ <code>19,987,500 تومان</code> را", text)

    def test_compact_admin_party_section_does_not_repeat_role_or_marker(self):
        from handlers.offers import _post_acceptance_admin_party_section_html

        with patch(
            "handlers.offers.get_user",
            return_value={
                "display_name": "کاربر نمونه",
                "username": "sample_user",
            },
        ):
            text = _post_acceptance_admin_party_section_html(
                {"operation": "فروش", "methods": "IBAN"},
                {
                    "owner_id": 222,
                    "proposer_telegram_id": 111,
                },
                party="seller",
                buyer_country="آلمان",
                seller_country="آلمان",
                fin_html="🧮 نهایی: <code>19,012,500 تومان</code>\n",
                accounts_text=(
                    "فرناز فرجی\nبانک پاسارگاد\n\n"
                    "📷 عکس حساب (ثبت‌شده)"
                ),
                accounts_status_mode=True,
                account_embedded_photo=False,
                compact=True,
            )

        self.assertIn("<b>مشخصات:</b>", text)
        self.assertNotIn("<b>فروشنده یورو:</b>", text)
        self.assertIn("📷 عکس در پیام زیر", text)
        self.assertIn("فرناز فرجی\nبانک پاسارگاد", text)
        self.assertNotIn("📷 عکس حساب (ثبت‌شده)", text)

    def test_no_deal_path_adds_a_second_unit_after_copyable_helper(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "handlers/deal_gate.py",
            "handlers/offers.py",
            "scripts/resend_toman_card_to_buyer.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"_copyable_toman_html\([^\n]+\)\}\s*تومان",
                relative,
            )


if __name__ == "__main__":
    unittest.main()
