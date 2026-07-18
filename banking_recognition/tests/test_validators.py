"""Tests for Iranian banking validators.

All account/card values in this file are synthetic test fixtures.
"""

import unittest

from banking_recognition.banks.database import (
    detect_bank_from_card,
    detect_bank_from_sheba,
)
from banking_recognition.validators.iran_banking import (
    cross_validate_fields,
    luhn_check,
    sheba_mod97,
    validate_amount_rial,
    validate_card,
    validate_jdate,
    validate_sheba,
    validate_time,
)


class BankingValidatorTests(unittest.TestCase):
    def test_luhn_accepts_known_synthetic_cards(self):
        self.assertTrue(luhn_check("6037990000000006"))
        self.assertTrue(luhn_check("4111111111111111"))
        self.assertFalse(luhn_check("6037990000000005"))

    def test_card_validation_rejects_bad_or_repeated_numbers(self):
        self.assertEqual(validate_card("6037-9900-0000-0006"), (True, ""))
        self.assertEqual(validate_card("6037990000000005"), (False, "luhn"))
        self.assertEqual(
            validate_card("0000000000000000"),
            (False, "card_repeated_digits"),
        )
        self.assertEqual(validate_card("123"), (False, "card_length"))

    def test_sheba_checksum_and_normalization(self):
        valid = "IR380170000000100000000001"
        self.assertTrue(sheba_mod97(valid))
        self.assertEqual(validate_sheba("IR38 0170 0000 0010 0000 0000 01"), (True, ""))
        self.assertEqual(validate_sheba(valid[:-1] + "2"), (False, "sheba_mod97"))
        self.assertEqual(validate_sheba("IR12"), (False, "sheba_length"))

    def test_bank_detection_uses_known_prefixes(self):
        self.assertTrue(detect_bank_from_card("6037990000000006"))
        self.assertTrue(detect_bank_from_sheba("IR380170000000100000000001"))

    def test_amount_boundaries(self):
        self.assertEqual(validate_amount_rial(None), (False, "amount_missing"))
        self.assertEqual(validate_amount_rial(9_999), (False, "amount_too_small"))
        self.assertEqual(validate_amount_rial(10_000), (True, ""))
        self.assertEqual(validate_amount_rial(50_000_000_000), (True, ""))
        self.assertEqual(
            validate_amount_rial(50_000_000_001),
            (False, "amount_too_large"),
        )

    def test_jalali_date_month_day_ranges(self):
        self.assertEqual(validate_jdate("1402/06/31"), (True, ""))
        self.assertEqual(validate_jdate("1402/07/30"), (True, ""))
        self.assertEqual(validate_jdate("1402/07/31"), (False, "jdate_range"))
        self.assertEqual(validate_jdate("1402/12/31"), (False, "jdate_range"))
        self.assertEqual(validate_jdate("1402-01-01"), (False, "jdate_format"))

    def test_time_format_and_clock_ranges(self):
        for value in ("", "0:00", "09:05", "23:59:59"):
            with self.subTest(value=value):
                self.assertEqual(validate_time(value), (True, ""))
        for value in ("24:00", "12:60", "12:30:60"):
            with self.subTest(value=value):
                self.assertEqual(validate_time(value), (False, "time_range"))
        self.assertEqual(validate_time("9.30"), (False, "time_format"))

    def test_cross_validation_collects_field_errors(self):
        errors = cross_validate_fields(
            card_number="0000000000000000",
            sheba="IR12",
            bank_name="",
            amount=1,
        )
        self.assertEqual(
            errors,
            [
                "card:card_repeated_digits",
                "sheba:sheba_length",
                "amount:amount_too_small",
            ],
        )


if __name__ == "__main__":
    unittest.main()
