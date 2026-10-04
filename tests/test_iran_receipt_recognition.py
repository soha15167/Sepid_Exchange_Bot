"""Safety regressions for Persian Iran-panel receipt recognition."""

from __future__ import annotations

import unittest

from banking_recognition.pipeline import _from_llm_dict, _merge_google_vision
from banking_recognition.banks.database import detect_bank_from_card, detect_bank_from_text
from banking_recognition.extractors.universal_extractor import extract_status
from banking_recognition.models.schemas import BankingExtractionResult
from banking_recognition.ocr.engine import OcrRun
from handlers.iran_panel_sync import (
    _assess_receipt_payload,
    _draft_keyboard,
    _deal_description_from_caption,
    _deal_buyer_name_from_caption,
    _guess_transfer_type_from_receipt,
    _panel_payload_for_submit,
    _parse_payload_in,
    _parse_payload_out,
    _payload_from_vision,
    _vision_dict_from_banking,
    _render_draft_html,
)


class ReceiptRecognitionSafetyTests(unittest.TestCase):
    def test_empty_text_does_not_invent_bank_or_transfer_type(self):
        incoming, _ = _parse_payload_in("")
        outgoing, _ = _parse_payload_out("")

        self.assertEqual(incoming["bank_name"], "")
        self.assertEqual(incoming["transfer_type"], "")
        self.assertEqual(outgoing["bank_name"], "")
        self.assertEqual(outgoing["transfer_type"], "")
        self.assertEqual(_panel_payload_for_submit(incoming, "in")["transfer_type"], "")

    def test_toman_from_model_is_converted_to_rial_once(self):
        fields = _from_llm_dict({"amount": "۱۲۳۴۵۰۰", "currency": "toman"})
        self.assertEqual(fields["amount"], 12_345_000)

    def test_direction_mismatch_requires_admin_review(self):
        assessed = _assess_receipt_payload(
            {"iran_amount": 20_000_000},
            "برداشت از حساب مبلغ ۲۰۰۰۰۰۰۰ ریال",
            "in",
            "ocr",
        )
        self.assertEqual(assessed["_detected_direction"], "out")
        self.assertTrue(assessed["_recognition_blocking"])
        self.assertIn("جهت روی رسید", assessed["_recognition_warnings"][0])

    def test_failed_receipt_requires_admin_review(self):
        assessed = _assess_receipt_payload(
            {"_receipt_status": "ناموفق"}, "", "out", "gemini"
        )
        self.assertTrue(assessed["_recognition_blocking"])
        self.assertTrue(any("ناموفق" in x for x in assessed["_recognition_warnings"]))

    def test_correct_direction_has_no_warning(self):
        assessed = _assess_receipt_payload(
            {}, "حساب شما بستانکار شد", "in", "ocr"
        )
        self.assertFalse(assessed["_recognition_blocking"])

    def test_incoming_uses_sender_and_destination_bank(self):
        data = {
            "bank_name": "بانک نمایش‌دهنده",
            "owner_name": "صاحب حساب مقصد",
            "sender_name": "فرستنده واقعی",
            "receiver_name": "گیرنده",
            "confidence": 91,
            "source": "gemini",
            "meta": {
                "source_bank": "ملت",
                "destination_bank": "ملی",
                "detected_direction": "in",
            },
        }
        vision = _vision_dict_from_banking(data, "in")
        payload = _payload_from_vision(vision, "in")
        self.assertEqual(payload["bank_name"], "ملی")
        self.assertEqual(payload["depositor_name"], "فرستنده واقعی")

    def test_outgoing_uses_destination_holder_not_source_holder(self):
        data = {
            "bank_name": "بانک نمایش‌دهنده",
            "owner_name": "صاحب حساب منبع",
            "sender_name": "فرستنده واقعی",
            "receiver_name": "فروشنده مقصد",
            "transfer_type": "پایا",
            "confidence": 94,
            "source": "gemini",
            "meta": {
                "source_bank": "ملی",
                "destination_bank": "صادرات",
                "detected_direction": "out",
            },
        }
        vision = _vision_dict_from_banking(data, "out")
        payload = _payload_from_vision(vision, "out")
        self.assertEqual(payload["bank_name"], "ملی")
        self.assertEqual(payload["dest_bank"], "صادرات")
        self.assertEqual(payload["depositor_name"], "فروشنده مقصد")
        self.assertEqual(payload["_destination_account_holder"], "فروشنده مقصد")
        self.assertEqual(payload["transfer_type"], "پایا")

    def test_account_product_text_is_never_used_as_outgoing_person_name(self):
        data = {
            "owner_name": "برداشت از حساب کوتاه مدت اشخاص حقیقی انفرادی",
            "sender_name": "حسن نصیری",
            "receiver_name": "امیر صمدیار",
            "source": "gemini",
            "meta": {"detected_direction": "out"},
        }
        payload = _payload_from_vision(_vision_dict_from_banking(data, "out"), "out")
        self.assertEqual(payload["depositor_name"], "امیر صمدیار")
        self.assertEqual(payload["_destination_account_holder"], "امیر صمدیار")
        rendered = _render_draft_html("out", payload)
        self.assertIn("نام برداشت‌کننده (صاحب حساب مقصد)", rendered)
        self.assertIn("امیر صمدیار", rendered)
        self.assertNotIn("حسن نصیری", rendered)
        self.assertNotIn("کوتاه مدت", rendered)

    def test_ocr_enrichment_does_not_overwrite_destination_with_source_product(self):
        from handlers.iran_panel_sync import _enrich_payload_from_ocr_text

        raw = """برداشت از حساب: کوتاه مدت اشخاص حقیقی انفرادی
به نام: حسن نصیری
واریز به حساب: سپرده قرض الحسنه پس انداز
به نام: امیر صمدیار"""
        payload = _enrich_payload_from_ocr_text(
            {"depositor_name": "امیر صمدیار", "iran_amount": 299_425_000},
            raw,
            "out",
        )
        self.assertEqual(payload["depositor_name"], "امیر صمدیار")

    def test_ocr_fallback_extracts_destination_holder_from_destination_section(self):
        from handlers.iran_panel_sync import _enrich_payload_from_ocr_text

        raw = """برداشت از حساب: کوتاه مدت اشخاص حقیقی انفرادی
به نام: حسن نصیری
واریز به حساب: سپرده قرض الحسنه پس انداز
به نام: امیر صمدیار"""
        payload = _enrich_payload_from_ocr_text(
            {"depositor_name": "", "iran_amount": 299_425_000}, raw, "out"
        )
        self.assertEqual(payload["depositor_name"], "امیر صمدیار")

    def test_deal_number_from_caption_becomes_description(self):
        caption = "📌 آگهی ۳۴۴۰ · پیشنهاد ۱ · #236\nفیش تومان به فروشنده"
        self.assertEqual(_deal_description_from_caption(caption), "آگهی 3440")
        self.assertEqual(_deal_description_from_caption("شماره پیگیری 3440"), "")

    def test_incoming_deal_uses_registered_buyer_display_name(self):
        from unittest.mock import patch

        caption = "📌 آگهی ۳۴۵۳ · پیشنهاد ۱ · #258\nفیش تومان به فروشنده"
        with (
            patch(
                "database.db.get_offer_by_advert_and_seq",
                return_value={"id": 258, "proposer_telegram_id": 222},
            ),
            patch(
                "database.db.deal_gate_get",
                return_value={"buyer_telegram_id": 1462958044},
            ),
            patch(
                "database.db.get_user",
                return_value={
                    "display_name": "nongb",
                    "username": "nnshiinn",
                    "full_name": "fallback name",
                },
            ),
        ):
            self.assertEqual(_deal_buyer_name_from_caption(caption), "nongb")

    def test_bank_names_are_submitted_without_bank_prefix(self):
        outgoing = _panel_payload_for_submit(
            {
                "bank_name": "بانک ملی",
                "dest_bank": "بانک صادرات ایران",
                "iran_amount": 20_000_000,
            },
            "out",
        )
        incoming = _panel_payload_for_submit(
            {"bank_name": "بانک ملت", "iran_amount": 20_000_000}, "in"
        )
        self.assertEqual(outgoing["bank_name"], "ملی")
        self.assertEqual(outgoing["destination_bank"], "صادرات")
        self.assertEqual(incoming["bank_name"], "ملت")

    def test_mellat_internal_transfer_is_account_to_account(self):
        raw = """بانک ملت
رسید انتقال وجه ملت
برداشت از حساب: کوتاه مدت
واریز به حساب: سپرده قرض الحسنه"""
        from handlers.iran_panel_sync import _enrich_payload_from_ocr_text

        payload = _enrich_payload_from_ocr_text(
            {
                "bank_name": "ملت",
                "dest_bank": "",
                "transfer_type": "انتقال وجه ملت",
                "iran_amount": 299_425_000,
            },
            raw,
            "out",
        )
        self.assertEqual(payload["transfer_type"], "حساب به حساب")
        self.assertEqual(payload["dest_bank"], "ملت")

    def test_outgoing_submit_uses_panel_destination_bank_field_name(self):
        submitted = _panel_payload_for_submit(
            {
                "bank_name": "ملت",
                "dest_bank": "ملت",
                "iran_amount": 299_425_000,
                "depositor_name": "امیر صمدیار",
                "transfer_type": "حساب به حساب",
                "description": "آگهی 3440",
                "jdate": "1405/04/26",
            },
            "out",
        )
        self.assertEqual(submitted["destination_bank"], "ملت")
        self.assertNotIn("dest_bank", submitted)

    def test_outgoing_destination_bank_comes_from_iban_when_logo_has_no_text(self):
        vision = _vision_dict_from_banking(
            {
                "bank_name": "ملی",
                "sheba": "IR55016000000000216497315",
                "confidence": 90,
                "source": "gemini",
                "meta": {},
            },
            "out",
        )
        self.assertEqual(vision["dest_bank"], "کشاورزی")

    def test_google_vision_corroborates_exact_rial_amount(self):
        result = BankingExtractionResult(
            amount=204_750_000, confidence=100, source="gemini"
        )
        merged = _merge_google_vision(
            result,
            OcrRun("google_vision", "مبلغ: 204,750,000 ریال", 95),
        )
        self.assertEqual(merged.amount, 204_750_000)
        self.assertTrue(merged.meta["amount_corroborated"])
        self.assertNotIn("amount_disagreement", merged.meta)

    def test_google_vision_disagreement_never_changes_amount(self):
        result = BankingExtractionResult(
            amount=204_750_000, confidence=100, source="gemini"
        )
        merged = _merge_google_vision(
            result,
            OcrRun("google_vision", "مبلغ: 20,475,000 ریال", 95),
        )
        self.assertEqual(merged.amount, 204_750_000)
        self.assertEqual(
            merged.meta["amount_disagreement"]["google_vision"], 20_475_000
        )
        self.assertLess(merged.confidence, 70)

    def test_cloud_amount_disagreement_requires_admin_review(self):
        assessed = _assess_receipt_payload(
            {
                "iran_amount": 204_750_000,
                "_amount_disagreement": {
                    "gemini": 204_750_000,
                    "google_vision": 20_475_000,
                },
            },
            "",
            "out",
            "gemini+google_vision",
        )
        self.assertTrue(assessed["_recognition_blocking"])
        self.assertTrue(
            any("Google OCR" in x for x in assessed["_recognition_warnings"])
        )

    def test_generic_vision_safety_fields_are_preserved(self):
        payload = _payload_from_vision(
            {
                "confidence": 62,
                "detected_direction": "out",
                "status": "ناموفق",
                "currency": "rial",
            },
            "out",
        )
        self.assertEqual(payload["_recognition_score"], 62)
        self.assertEqual(payload["_detected_direction"], "out")
        self.assertEqual(payload["_receipt_status"], "ناموفق")

    def test_warning_keyboard_requires_review_before_submit(self):
        markup = _draft_keyboard(can_submit=True, needs_review=True)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("tx|reviewed", callbacks)
        self.assertNotIn("tx|submit", callbacks)

    def test_multiple_banks_are_detected_without_melli_default(self):
        self.assertEqual(detect_bank_from_text("بانک اقتصاد نوین"), "اقتصاد نوین")
        self.assertEqual(detect_bank_from_text("بانک خاورمیانه"), "خاورمیانه")
        self.assertEqual(detect_bank_from_text("پست بانک ایران"), "پست بانک")
        self.assertEqual(detect_bank_from_card("6037691234567890"), "صادرات")

    def test_non_card_transaction_types_are_recognized(self):
        cases = {
            "انتقال از طریق ساتنا": "ساتنا",
            "حواله پایا انجام شد": "پایا",
            "انتقال حساب به حساب": "حساب به حساب",
            "خرید از پایانه فروش": "خرید",
            "پرداخت قبض برق": "پرداخت قبض",
            "واریز حقوق ماهانه": "حقوق",
            "برگشت وجه خرید": "برگشت وجه",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_guess_transfer_type_from_receipt(text), expected)

    def test_unsuccessful_is_not_misread_as_successful(self):
        self.assertEqual(extract_status("تراکنش ناموفق بود"), "ناموفق")


if __name__ == "__main__":
    unittest.main()
