"""خط لولهٔ اصلی: preprocess → OCR → extract → validate → confidence → Gemini."""

from __future__ import annotations

import logging
import time

from banking_recognition.classifiers.document_classifier import classify_document
from banking_recognition.config import (
    BANKING_RECOGNITION_ENABLED,
    GEMINI_ENABLED,
    GEMINI_MODEL,
    LLM_CONFIDENCE_THRESHOLD,
)
from banking_recognition.extractors.universal_extractor import extract_all_fields
from banking_recognition.fraud.detector import compute_fraud_score
from banking_recognition.models.schemas import BankingExtractionResult
from banking_recognition.ocr.engine import run_best_ocr
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
    return result


def _gemini_amount_usable(result: BankingExtractionResult) -> bool:
    try:
        amt = int(result.amount or 0)
    except (TypeError, ValueError):
        return False
    return amt >= 1_000_000


async def run_pipeline_gemini_first(image_path: str) -> BankingExtractionResult:
    """رسید تلگرام: ابتدا Gemini؛ در صورت شکست → OCR معمولی."""
    t0 = time.perf_counter()
    if not BANKING_RECOGNITION_ENABLED:
        r = BankingExtractionResult(raw_text="", source="disabled")
        r.meta["disabled"] = True
        r.processing_ms = int((time.perf_counter() - t0) * 1000)
        return r

    if GEMINI_ENABLED:
        llm = await extract_with_gemini(image_path)
        if llm:
            result = _result_from_gemini(llm, image_path=image_path)
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

    result = await run_pipeline(image_path, skip_gemini=True)
    result.processing_ms = int((time.perf_counter() - t0) * 1000)
    return result


async def run_pipeline(image_path: str, *, skip_gemini: bool = False) -> BankingExtractionResult:
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
        llm = await extract_with_gemini(image_path, ocr_hint=ocr_run.text)
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
        else:
            result.source = "ocr"
    else:
        result.source = "ocr"

    result.fraud_score = compute_fraud_score(
        raw_text=result.raw_text,
        fields=result.model_dump(),
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
