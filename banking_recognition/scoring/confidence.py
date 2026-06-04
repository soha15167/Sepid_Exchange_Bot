"""امتیاز اطمینان ۰–۱۰۰."""

from __future__ import annotations

from banking_recognition.models.schemas import BankingExtractionResult, DocumentType


def compute_confidence(
    *,
    ocr_score: float,
    fields: dict,
    validation_errors: list[str],
    document_type: str,
) -> float:
    score = min(40.0, ocr_score * 0.4)
    filled = 0
    weights = {
        "card_number": 12,
        "sheba": 12,
        "amount": 15,
        "tracking_number": 10,
        "date": 8,
        "owner_name": 8,
        "bank_name": 5,
    }
    for key, w in weights.items():
        val = fields.get(key)
        if val is None:
            continue
        if isinstance(val, int) and val > 0:
            score += w
            filled += 1
        elif isinstance(val, str) and str(val).strip():
            score += w
            filled += 1

    if document_type != DocumentType.UNKNOWN.value:
        score += 8
    if document_type in (
        DocumentType.BANK_RECEIPT.value,
        DocumentType.PAYMENT_CONFIRMATION.value,
    ):
        if fields.get("amount"):
            score += 5

    score -= min(30, len(validation_errors) * 8)
    return max(0.0, min(100.0, round(score, 1)))


def merge_llm_boost(base: float, llm_fields: dict, prev: BankingExtractionResult) -> float:
    """پس از LLM اگر فیلدهای کلیدی پر شدند امتیاز بالا می‌رود."""
    boost = base
    if llm_fields.get("amount") and not prev.amount:
        boost += 15
    if llm_fields.get("card_number") and not prev.card_number:
        boost += 10
    if llm_fields.get("sheba") and not prev.sheba:
        boost += 10
    if llm_fields.get("tracking_number") and not prev.tracking_number:
        boost += 8
    return min(100.0, boost + 10)
