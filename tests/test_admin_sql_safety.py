"""Regression tests for admin-selected SQL column names."""

import unittest

from handlers.admin import _is_admin_editable_advert_value_field


class AdminSqlSafetyTests(unittest.TestCase):
    def test_only_expected_advert_value_fields_are_allowed(self):
        expected = {
            "full_name",
            "euro_amount",
            "rate_toman",
            "description",
            "account_country",
            "fee_override_eur",
        }
        for field in expected:
            with self.subTest(field=field):
                self.assertTrue(_is_admin_editable_advert_value_field(field))

        rejected = {
            "methods",
            "instant_transfer",
            "user_id",
            "status",
            "full_name = 'x' WHERE 1=1 --",
            "",
            None,
            123,
        }
        for field in rejected:
            with self.subTest(field=field):
                self.assertFalse(_is_admin_editable_advert_value_field(field))


if __name__ == "__main__":
    unittest.main()
