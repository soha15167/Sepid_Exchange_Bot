"""Captioned account photos must remain visible in the admin deal album."""

from __future__ import annotations

import unittest


class CaptionedAccountPhotoTests(unittest.TestCase):
    def test_deal_gate_recognizes_marker_after_photo_caption(self):
        from handlers.deal_gate import (
            _account_text_is_photo_marker,
            _admin_party_account_photo,
        )

        saved_text = "فرناز فرجی\nبانک پاسارگاد\n\n📷 عکس حساب (ثبت‌شده)"
        gate = {
            "seller_accounts_text": saved_text,
            "seller_accounts_photo_file_id": "telegram-file-id-252",
        }

        self.assertTrue(_account_text_is_photo_marker(saved_text))
        self.assertEqual(
            _admin_party_account_photo(gate, "seller"),
            "telegram-file-id-252",
        )

    def test_admin_message_renderer_recognizes_captioned_photo(self):
        from handlers.offers import _account_text_is_photo_marker

        self.assertTrue(
            _account_text_is_photo_marker(
                "فرناز فرجی\nبانک پاسارگاد\n\n📷 عکس حساب (ثبت‌شده)"
            )
        )

    def test_normal_text_with_unrelated_camera_emoji_is_not_photo_marker(self):
        from handlers.deal_gate import _account_text_is_photo_marker as gate_check
        from handlers.offers import _account_text_is_photo_marker as offer_check

        text = "📷 تصویر کارت را بعداً ارسال می‌کنم"
        self.assertFalse(gate_check(text))
        self.assertFalse(offer_check(text))


if __name__ == "__main__":
    unittest.main()
