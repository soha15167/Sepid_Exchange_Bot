"""سیگنال‌های اولیه تقلب — بدون ادعای قطعیت."""

from __future__ import annotations

import re

from banking_recognition.validators.iran_banking import validate_card, validate_sheba


def compute_fraud_score(
    *,
    raw_text: str,
    fields: dict,
    validation_errors: list[str],
    image_path: str,
) -> float:
    score = 0.0
    low = (raw_text or "").lower()

    if not raw_text.strip():
        score += 40
    if len(validation_errors) >= 2:
        score += 25
    if "photoshop" in low or "edited" in low:
        score += 30

    card = fields.get("card_number") or ""
    sheba = fields.get("sheba") or ""
    amount = fields.get("amount")

    if card:
        ok, _ = validate_card(card)
        if not ok:
            score += 20
    if sheba:
        ok, _ = validate_sheba(sheba)
        if not ok:
            score += 20

    if amount and re.search(r"پیگیری", low):
        tr = re.sub(r"\D", "", fields.get("tracking_number") or "")
        amt_s = str(amount)
        if tr and amt_s in tr:
            score += 35

    if card and sheba:
        card_bank = (fields.get("bank_name") or "").strip()
        if card_bank and "mismatch" in " ".join(validation_errors):
            score += 15

    # کیفیت بسیار کم OCR
    if len(raw_text) < 40 and (card or sheba or amount):
        score += 10

    _ = image_path
    return min(100.0, round(score, 1))
