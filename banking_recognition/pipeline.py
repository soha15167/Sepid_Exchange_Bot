"""خط لولهٔ اصلی: preprocess → OCR → extract → validate → confidence → Gemini."""

from __future__ import annotations

import asyncio
import logging
import time

from banking_recognition.classifiers.document_classifier import classify_document
from banking_recognition.config import (
    BANKING_RECOGNITION_ENABLED,
    GEMINI_ENABLED,
    GEMINI_MODEL,
    GOOGLE_VISION_ENABLED,
    LLM_CONFIDENCE_THRESHOLD,
)
from banking_recognition.extractors.universal_extractor import extract_all_fields
from banking_recognition.fraud.detector import compute_fraud_score
from banking_recognition.models.schemas import BankingExtractionResult
from banking_recognition.ocr.engine import run_best_ocr
from banking_recognition.ocr.google_vision import run_google_vision_ocr
from banking_recognition.preprocessing.image_preprocess import preprocess_image_path
from banking_recognition.scoring.confidence import compute_confidence, merge_llm_boost
from banking_recognition.validators.iran_banking import cross_validate_fields
from banking_recognition.vision.gemini_fallback import extract_with_gemini

logger = logging.getLogger(__name__)


def _apply_fields(result: BankingExtractionResult, fields: dict) -> None:
    for key in (
        "bank_name",
        "card_number",
        "sheba",
        "account_number",
        "owner_name",
        "sender_name",
        "receiver_name",
        "transfer_type",
        "date",
        "time",
        "tracking_number",
        "transaction_id",
        "status",
    ):
        val = fields.get(key)
        if val is not None and str(val).strip():
            setattr(result, key, str(val).strip())
    amt = fields.get("amount")
    if amt is not None:
        try:
            if isinstance(amt, str):
                from utils.iran_digits import digits_only_ascii

                amt = digits_only_ascii(amt)
            result.amount = int(amt)
        except (TypeError, ValueError):
            pass


def _from_llm_dict(data: dict) -> dict:
    out: dict = {}
    mapping = {
        "document_type": "document_type",
        "bank_name": "bank_name",
        "card_number": "card_number",
        "sheba": "sheba",
        "account_number": "account_number",
        "owner_name": "owner_name",
        "sender_name": "sender_name",
        "receiver_name": "receiver_name",
        "transfer_type": "transfer_type",
        "amount": "amount",
        "iran_amount": "amount",
        "date": "date",
        "jdate": "date",
        "time": "time",
        "tracking_number": "tracking_number",
        "transaction_id": "transaction_id",
        "status": "status",
    }
    for src, dst in mapping.items():
        if src in data and data[src] not in (None, ""):
            out[dst] = data[src]
    amount = out.get("amount")
    currency = str(data.get("currency") or "").strip().lower()
    if amount not in (None, ""):
        try:
            if isinstance(amount, str):
                from utils.iran_digits import digits_only_ascii

                amount = digits_only_ascii(amount)
            amount = int(amount)
            if currency in ("toman", "تومان"):
                amount *= 10
            out["amount"] = amount
        except (TypeError, ValueError):
            out.pop("amount", None)
    return out


def _result_from_gemini(llm: dict, *, image_path: str) -> BankingExtractionResult:
    """ساخت نتیجه فقط از پاسخ Gemini (بدون OCR)."""
    result = BankingExtractionResult(raw_text="", source="gemini")
    llm_fields = _from_llm_dict(llm)
    _apply_fields(result, llm_fields)
    if llm.get("document_type"):
        result.document_type = str(llm["document_type"])
    result.validation_errors = cross_validate_fields(
        card_number=result.card_number,
        sheba=result.sheba,
        bank_name=result.bank_name,
        amount=result.amount,
    )
    result.confidence = merge_llm_boost(
        float(llm.get("confidence") or 85),
        llm_fields,
        result,
    )
    result.fraud_score = compute_fraud_score(
        raw_text="",
        fields=llm_fields,
        validation_errors=result.validation_errors,
        image_path=image_path,
    )
    result.meta["llm"] = GEMINI_MODEL
    for key in (
        "detected_direction",
        "currency",
        "source_bank",
        "destination_bank",
    ):
        if llm.get(key) not in (None, ""):
            result.meta[key] = llm[key]
    return result


def _gemini_amount_usable(result: BankingExtractionResult) -> bool:
    try:
        amt = int(result.amount or 0)
    except (TypeError, ValueError):
        return False
    return amt >= 1_000_000


def _result_from_ocr_run(ocr_run, *, image_path: str) -> BankingExtractionResult:
    """Build a validated result from a cloud/local OCR text run."""
    result = BankingExtractionResult(raw_text=ocr_run.text, source=ocr_run.engine)
    result.meta["ocr_engine"] = ocr_run.engine
    fields = extract_all_fields(ocr_run.text)
    _apply_fields(result, fields)
    result.document_type = classify_document(
        ocr_run.text,
        has_card=bool(result.card_number),
        has_sheba=bool(result.sheba),
        has_amount=result.amount is not None,
    )
    result.validation_errors = cross_validate_fields(
        card_number=result.card_number,
        sheba=result.sheba,
        bank_name=result.bank_name,
        amount=result.amount,
    )
    result.confidence = compute_confidence(
        ocr_score=ocr_run.score,
        fields=fields,
        validation_errors=result.validation_errors,
        document_type=result.document_type,
    )
    result.fraud_score = compute_fraud_score(
        raw_text=ocr_run.text,
        fields=fields,
        validation_errors=result.validation_errors,
        image_path=image_path,
    )
    return result


def _merge_google_vision(
    result: BankingExtractionResult, google_run
) -> BankingExtractionResult:
    """Corroborate exact values; never silently replace a conflicting amount."""
    if not google_run:
        result.meta["google_vision"] = "unavailable"
        return result

    google_fields = extract_all_fields(google_run.text)
    google_amount = google_fields.get("amount")
    try:
        google_amount = int(google_amount) if google_amount is not None else None
    except (TypeError, ValueError):
        google_amount = None

    result.raw_text = google_run.text
    result.meta["ocr_engine"] = "google_vision"
    result.meta["google_vision_score"] = float(google_run.score or 0)
    result.meta["google_vision_amount"] = google_amount

    current_amount = int(result.amount or 0)
    if current_amount and google_amount:
        if current_amount == google_amount:
            result.meta["amount_corroborated"] = True
        else:
            result.meta["amount_disagreement"] = {
                "gemini": current_amount,
                "google_vision": google_amount,
            }
            result.confidence = min(float(result.confidence or 0), 60.0)
    elif not current_amount and google_amount:
        result.amount = google_amount
        result.meta["amount_from"] = "google_vision"

    if result.source == "gemini":
        result.source = "gemini+google_vision"
    return result


async def run_pipeline_gemini_first(
    image_path: str, *, mode: str = ""
) -> BankingExtractionResult:
    """رسید تلگرام: ابتدا Gemini؛ در صورت شکست → OCR معمولی."""
    t0 = time.perf_counter()
    if not BANKING_RECOGNITION_ENABLED:
        r = BankingExtractionResult(raw_text="", source="disabled")
        r.meta["disabled"] = True
        r.processing_ms = int((time.perf_counter() - t0) * 1000)
        return r

    google_task = None
    google_run = None
    if GOOGLE_VISION_ENABLED:
        google_task = asyncio.create_task(
            asyncio.to_thread(run_google_vision_ocr, image_path)
        )

    if GEMINI_ENABLED:
        llm = await extract_with_gemini(image_path, mode=mode)
        if google_task:
            google_run = await google_task
        if llm:
            result = _result_from_gemini(llm, image_path=image_path)
            result = _merge_google_vision(result, google_run)
            if _gemini_amount_usable(result) or result.confidence >= 70:
                result.processing_ms = int((time.perf_counter() - t0) * 1000)
                logger.info(
                    "banking_recognition gemini-first ok conf=%.1f amount=%s ms=%s",
                    result.confidence,
                    result.amount,
                    result.processing_ms,
                )
                return result
            logger.info(
                "banking_recognition gemini-first weak amount=%s conf=%.1f — OCR fallback",
                result.amount,
                result.confidence,
            )

    if google_task and google_run is None:
        google_run = await google_task
    if google_run:
        result = _result_from_ocr_run(google_run, image_path=image_path)
        if _gemini_amount_usable(result) or result.confidence >= 70:
            result.processing_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "banking_recognition google-vision fallback conf=%.1f amount=%s ms=%s",
                result.confidence,
                result.amount,
                result.processing_ms,
            )
            return result

    result = await run_pipeline(image_path, skip_gemini=True, mode=mode)
    result.processing_ms = int((time.perf_counter() - t0) * 1000)
    return result


async def run_pipeline(
    image_path: str, *, skip_gemini: bool = False, mode: str = ""
) -> BankingExtractionResult:
    t0 = time.perf_counter()
    result = BankingExtractionResult(raw_text="", source="ocr")

    if not BANKING_RECOGNITION_ENABLED:
        result.meta["disabled"] = True
        result.processing_ms = int((time.perf_counter() - t0) * 1000)
        return result

    work_path, prep_meta = preprocess_image_path(image_path)
    result.meta["preprocess"] = prep_meta

    ocr_run = run_best_ocr(work_path)
    result.raw_text = ocr_run.text
    result.meta["ocr_engine"] = ocr_run.engine

    fields = extract_all_fields(ocr_run.text)
    _apply_fields(result, fields)

    result.document_type = classify_document(
        ocr_run.text,
        has_card=bool(result.card_number),
        has_sheba=bool(result.sheba),
        has_amount=result.amount is not None,
    )

    result.validation_errors = cross_validate_fields(
        card_number=result.card_number,
        sheba=result.sheba,
        bank_name=result.bank_name,
        amount=result.amount,
    )

    result.confidence = compute_confidence(
        ocr_score=ocr_run.score,
        fields=fields,
        validation_errors=result.validation_errors,
        document_type=result.document_type,
    )

    result.fraud_score = compute_fraud_score(
        raw_text=ocr_run.text,
        fields=fields,
        validation_errors=result.validation_errors,
        image_path=image_path,
    )

    if not skip_gemini and result.confidence < LLM_CONFIDENCE_THRESHOLD:
        llm = await extract_with_gemini(image_path, ocr_hint=ocr_run.text, mode=mode)
        if llm:
            llm_fields = _from_llm_dict(llm)
            _apply_fields(result, llm_fields)
            if llm.get("document_type"):
                result.document_type = str(llm["document_type"])
            result.validation_errors = cross_validate_fields(
                card_number=result.card_number,
                sheba=result.sheba,
                bank_name=result.bank_name,
                amount=result.amount,
            )
            result.confidence = merge_llm_boost(
                float(llm.get("confidence") or result.confidence),
                llm_fields,
                result,
            )
            result.source = "ocr+gemini"
            result.meta["llm"] = GEMINI_MODEL if "gemini" in str(llm) else "gemini"
            for key in (
                "detected_direction",
                "currency",
                "source_bank",
                "destination_bank",
            ):
                if llm.get(key) not in (None, ""):
                    result.meta[key] = llm[key]
        else:
            result.source = "ocr"
    else:
        result.source = "ocr"

    result.fraud_score = compute_fraud_score(
        raw_text=result.raw_text,
        fields=result.to_telegram_dict(),
        validation_errors=result.validation_errors,
        image_path=image_path,
    )
    result.processing_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "banking_recognition done type=%s conf=%.1f fraud=%.1f ms=%s src=%s",
        result.document_type,
        result.confidence,
        result.fraud_score,
        result.processing_ms,
        result.source,
    )
    return result
