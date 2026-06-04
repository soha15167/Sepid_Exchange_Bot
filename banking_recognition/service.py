"""سرویس عمومی برای ربات تلگرام و FastAPI."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from banking_recognition.config import GEMINI_FIRST_FOR_RECEIPTS
from banking_recognition.pipeline import run_pipeline, run_pipeline_gemini_first
from banking_recognition.storage.log_db import log_processing

logger = logging.getLogger(__name__)


async def process_image_for_receipt(image_path: str) -> dict[str, Any]:
    """مسیر رسید پنل: Gemini قبل از OCR سنگین."""
    runner = (
        run_pipeline_gemini_first
        if GEMINI_FIRST_FOR_RECEIPTS
        else run_pipeline
    )
    result = await runner(image_path)
    out = result.to_telegram_dict()
    try:
        log_processing(
            image_path,
            ocr_text=result.raw_text,
            result_dict=out,
            processing_ms=result.processing_ms,
        )
    except Exception:
        logger.exception("banking_recognition log failed")
    return out


async def process_image(image_path: str) -> dict[str, Any]:
    """
    پردازش یک تصویر بانکی.
    برمی‌گرداند: dict JSON مطابق BankingExtractionResult
    """
    result = await run_pipeline(image_path)
    out = result.to_telegram_dict()
    try:
        log_processing(
            image_path,
            ocr_text=result.raw_text,
            result_dict=out,
            processing_ms=result.processing_ms,
        )
    except Exception:
        logger.exception("banking_recognition log failed")
    return out


def process_image_sync(image_path: str) -> dict[str, Any]:
    """نسخهٔ همگام برای فراخوانی از thread."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(process_image(image_path))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(process_image(image_path))).result()
