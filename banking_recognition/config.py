"""تنظیمات ماژول — از env یا config.settings."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _env_bool(key: str, default: bool = False) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key) or default)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key) or default)
    except (TypeError, ValueError):
        return default


BANKING_RECOGNITION_ENABLED = _env_bool("BANKING_RECOGNITION_ENABLED", True)
LLM_CONFIDENCE_THRESHOLD = _env_float("BANKING_LLM_CONFIDENCE_THRESHOLD", 85.0)
USE_PADDLE_OCR = _env_bool("BANKING_USE_PADDLE_OCR", False)
USE_EASYOCR = _env_bool("BANKING_USE_EASYOCR", False)
USE_TESSERACT_FALLBACK = _env_bool("BANKING_USE_TESSERACT", True)

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or os.getenv("BANKING_GEMINI_API_KEY")
    or ""
).strip()
GEMINI_MODEL = (os.getenv("BANKING_GEMINI_MODEL") or "gemini-2.0-flash-lite").strip()
GEMINI_TIMEOUT_SEC = _env_float("BANKING_GEMINI_TIMEOUT_SEC", 60.0)
GEMINI_MAX_RETRIES = _env_int("BANKING_GEMINI_MAX_RETRIES", 3)
GEMINI_RETRY_BASE_SEC = _env_float("BANKING_GEMINI_RETRY_BASE_SEC", 2.0)
_fallback_raw = (os.getenv("BANKING_GEMINI_MODEL_FALLBACKS") or "").strip()
GEMINI_MODEL_FALLBACKS: tuple[str, ...] = tuple(
    m.strip()
    for m in (
        _fallback_raw.split(",")
        if _fallback_raw
        else ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b")
    )
    if m.strip()
)
# برای /txin و رسید: اول Gemini، بعد OCR (جلوگیری از تایم‌اوت ۶۰ثانیهٔ Tesseract)
GEMINI_FIRST_FOR_RECEIPTS = _env_bool("BANKING_GEMINI_FIRST", True)
# روشن اگر کلید معتبر باشد؛ BANKING_GEMINI_ENABLED=0 آن را خاموش می‌کند
_gemini_flag = (os.getenv("BANKING_GEMINI_ENABLED") or "").strip().lower()
if _gemini_flag in ("0", "false", "no", "off"):
    GEMINI_ENABLED = False
else:
    GEMINI_ENABLED = bool(GEMINI_API_KEY) and (
        _gemini_flag in ("1", "true", "yes", "on") or not _gemini_flag
    )

LOG_DB_PATH = (
    os.getenv("BANKING_RECOGNITION_LOG_DB")
    or os.path.join(os.path.dirname(__file__), "..", "data", "banking_recognition.db")
)
