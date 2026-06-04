"""OCR: PaddleOCR → EasyOCR → Tesseract (موجود در پروژه)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from banking_recognition.config import (
    USE_EASYOCR,
    USE_PADDLE_OCR,
    USE_TESSERACT_FALLBACK,
)

logger = logging.getLogger(__name__)

_paddle_engine = None
_easy_reader = None


@dataclass
class OcrRun:
    engine: str
    text: str
    score: float


def _score_text(text: str) -> float:
    if not text:
        return 0.0
    from utils.receipt_ocr import ocr_text_quality_score

    base = ocr_text_quality_score(text)
    digits = len(re.sub(r"\D", "", text))
    persian = len(re.findall(r"[\u0600-\u06FF]", text))
    bonus = min(20, digits / 8) + min(15, persian / 20)
    return min(100.0, base + bonus)


def _run_paddle(image_path: str) -> OcrRun | None:
    global _paddle_engine
    if not USE_PADDLE_OCR:
        return None
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return None
    try:
        if _paddle_engine is None:
            _paddle_engine = PaddleOCR(
                use_angle_cls=True,
                lang="fa",
                show_log=False,
            )
        result = _paddle_engine.ocr(image_path, cls=True)
        lines: list[str] = []
        for block in result or []:
            if not block:
                continue
            for line in block:
                if line and len(line) >= 2 and line[1]:
                    lines.append(str(line[1][0]))
        text = "\n".join(lines)
        return OcrRun("paddle", text, _score_text(text))
    except Exception as e:
        logger.warning("banking_recognition paddle failed: %s", e)
        return None


def _run_easyocr(image_path: str) -> OcrRun | None:
    global _easy_reader
    if not USE_EASYOCR:
        return None
    try:
        import easyocr
    except ImportError:
        return None
    try:
        if _easy_reader is None:
            _easy_reader = easyocr.Reader(["fa", "en"], gpu=False)
        parts = _easy_reader.readtext(image_path, detail=0, paragraph=True)
        text = "\n".join(str(p) for p in parts if p)
        return OcrRun("easyocr", text, _score_text(text))
    except Exception as e:
        logger.warning("banking_recognition easyocr failed: %s", e)
        return None


def _run_tesseract(image_path: str) -> OcrRun | None:
    if not USE_TESSERACT_FALLBACK:
        return None
    try:
        from utils.receipt_ocr import ocr_image_to_text

        ok, text = ocr_image_to_text(image_path, quick=False)
        if not ok and not text:
            return None
        return OcrRun("tesseract", text or "", _score_text(text or ""))
    except Exception as e:
        logger.warning("banking_recognition tesseract failed: %s", e)
        return None


def run_best_ocr(image_path: str) -> OcrRun:
    runs: list[OcrRun] = []
    for fn in (_run_paddle, _run_easyocr, _run_tesseract):
        r = fn(image_path)
        if r and r.text.strip():
            runs.append(r)
    if not runs:
        return OcrRun("none", "", 0.0)
    runs.sort(key=lambda x: x.score, reverse=True)
    best = runs[0]
    logger.info(
        "banking_recognition ocr best=%s score=%.1f chars=%s",
        best.engine,
        best.score,
        len(best.text),
    )
    return best
