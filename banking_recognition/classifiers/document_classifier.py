"""طبقه‌بندی نوع سند از متن و سیگنال‌های بصری."""

from __future__ import annotations

import re

from banking_recognition.models.schemas import DocumentType


def classify_document(text: str, *, has_card: bool, has_sheba: bool, has_amount: bool) -> str:
    low = (text or "").lower()
    receipt_kw = (
        "پیگیری",
        "حواله",
        "انتقال",
        "واریز",
        "برداشت",
        "ریال",
        "مبلغ",
        "موفق",
        "رسید",
        "transaction",
    )
    card_kw = ("cvv", "cvv2", "انقضا", "expire", "شماره کارت", "card")
    sheba_kw = ("شبا", "sheba", "iban")

    receipt_score = sum(1 for k in receipt_kw if k in low)
    if has_amount and receipt_score >= 2:
        if "موفق" in low or "تایید" in low:
            return DocumentType.PAYMENT_CONFIRMATION.value
        return DocumentType.BANK_RECEIPT.value

    if has_card or any(k in low for k in card_kw):
        if receipt_score >= 1 and has_amount:
            return DocumentType.BANK_RECEIPT.value
        return DocumentType.BANK_CARD.value

    if has_sheba or any(k in low for k in sheba_kw):
        if receipt_score >= 1:
            return DocumentType.BANK_RECEIPT.value
        return DocumentType.SHEBA_DOCUMENT.value

    if re.search(r"نام\s*صاحب|حساب\s*جاری|شماره\s*حساب", low):
        return DocumentType.ACCOUNT_INFORMATION.value

    if receipt_score >= 1:
        return DocumentType.BANK_RECEIPT.value

    return DocumentType.UNKNOWN.value
