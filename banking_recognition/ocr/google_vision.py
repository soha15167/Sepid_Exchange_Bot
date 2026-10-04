"""Google Cloud Vision OCR for Persian banking documents.

The import is intentionally lazy so the bot can continue with Gemini and
Tesseract when the optional client library or cloud credentials are absent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from banking_recognition.config import (
    GOOGLE_VISION_ENABLED,
    GOOGLE_VISION_LANGUAGE_HINTS,
    GOOGLE_VISION_TIMEOUT_SEC,
)
from banking_recognition.ocr.engine import OcrRun, _score_text

logger = logging.getLogger(__name__)

_client = None


def run_google_vision_ocr(image_path: str) -> OcrRun | None:
    """Read dense Persian/English text without persisting the image in GCS."""
    global _client
    if not GOOGLE_VISION_ENABLED:
        return None
    try:
        from google.cloud import vision
    except ImportError:
        logger.warning("banking_recognition google vision client is not installed")
        return None

    try:
        if _client is None:
            _client = vision.ImageAnnotatorClient()
        content = Path(image_path).read_bytes()
        response = _client.document_text_detection(
            image=vision.Image(content=content),
            image_context=vision.ImageContext(
                language_hints=list(GOOGLE_VISION_LANGUAGE_HINTS)
            ),
            timeout=GOOGLE_VISION_TIMEOUT_SEC,
        )
        if response.error.message:
            logger.warning(
                "banking_recognition google vision api error code=%s",
                getattr(response.error, "code", "unknown"),
            )
            return None
        text = (response.full_text_annotation.text or "").strip()
        if not text:
            return None
        return OcrRun("google_vision", text, _score_text(text))
    except Exception as exc:
        logger.warning(
            "banking_recognition google vision failed: %s: %s",
            type(exc).__name__,
            str(exc)[:400],
        )
        return None
