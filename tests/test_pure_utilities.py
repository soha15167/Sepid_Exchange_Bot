"""Regression tests for deterministic utility functions."""

import unittest

from utils.bank_cards import BankCard, format_bank_card_html, parse_bank_cards
from utils.euro_fees import (
    advert_fee_override_eur,
    fee_per_side_eur,
    fee_total_eur,
    format_fee_eur,
)
from utils.iran_digits import digits_only_ascii, is_iranian_digit_char, normalize_digits
from utils.receipt_amount import normalize_transfer_amount, parse_rial_amount_text
from utils.validators import (
    is_valid_email,
    is_valid_phone,
    normalize_phone_input,
    phone_starts_with_plus,
)


class IranianDigitTests(unittest.TestCase):
    def test_normalizes_persian_and_arabic_indic_digits(self):
        self.assertEqual(normalize_digits("۱۲۳٤٥٦"), "123456")
        self.assertEqual(digits_only_ascii("کد ۱۲۳-٤٥٦"), "123456")

    def test_detects_supported_digit_characters(self):
        for char in ("1", "۱", "١"):
            self.assertTrue(is_iranian_digit_char(char))
        self.assertFalse(is_iranian_digit_char("x"))
        self.assertFalse(is_iranian_digit_char("12"))


class RegistrationValidatorTests(unittest.TestCase):
    def test_email_and_phone_formats(self):
        self.assertTrue(is_valid_email("user@example.com"))
        self.assertFalse(is_valid_email("user-at-example"))
        self.assertTrue(is_valid_phone("+491751234567"))
        self.assertFalse(is_valid_phone("00491751234567"))

    def test_phone_normalization(self):
        self.assertTrue(phone_starts_with_plus("\u200f+۹۸ ۹۱۲ ۱۲۳ ۴۵۶۷"))
        self.assertEqual(
            normalize_phone_input("\u200f+۹۸ (۹۱۲) ۱۲۳-۴۵۶۷"),
            "+989121234567",
        )
        self.assertEqual(normalize_phone_input("0049 175 1234567"), "+491751234567")


class ReceiptAmountTests(unittest.TestCase):
    def test_parses_persian_rial_amount(self):
        self.assertEqual(parse_rial_amount_text("۵۸,۸۰۰,۰۰۰"), 58_800_000)

    def test_corrects_one_extra_ocr_zero(self):
        self.assertEqual(normalize_transfer_amount(588_000_000), 58_800_000)

    def test_rejects_card_like_number(self):
        self.assertEqual(normalize_transfer_amount(6_037_990_000_000_006), 0)


class EuroFeeTests(unittest.TestCase):
    def test_fee_tier_boundary(self):
        self.assertEqual(fee_total_eur(500), 2.5)
        self.assertEqual(fee_total_eur(501), 2.505)
        self.assertEqual(fee_per_side_eur(500), 2.5)

    def test_fee_override_preserves_explicit_zero(self):
        self.assertEqual(advert_fee_override_eur({"fee_override_eur": "0"}), 0.0)
        self.assertIsNone(advert_fee_override_eur({"fee_override_eur": ""}))
        self.assertIsNone(advert_fee_override_eur({"fee_override_eur": -1}))
        self.assertEqual(fee_total_eur(999, 0), 0.0)
        self.assertEqual(format_fee_eur(999, 0), "0 یورو")


class BankCardFormattingTests(unittest.TestCase):
    def test_parser_skips_incomplete_entries(self):
        cards = parse_bank_cards(
            [
                {"id": "main", "title": "ملی", "card": "6037-9900-0000-0006"},
                {"id": "", "title": "missing id"},
                "not-a-dict",
            ]
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].card, "6037990000000006")

    def test_html_formatter_escapes_admin_controlled_values(self):
        rendered = format_bank_card_html(
            BankCard(
                id="x",
                title="ملی <script>",
                card="6037990000000006",
                iban="IR12&TEST",
            )
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("IR12&amp;TEST", rendered)


if __name__ == "__main__":
    unittest.main()
