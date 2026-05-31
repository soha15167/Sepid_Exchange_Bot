"""
utils/iran_digits.py — ارقام رسید بانکی ایران

مهم: «فارسی» و «عربی» یکی نیستند (یونیکد جدا):
  • فارسی (Farsi):  ۰۱۲۳۴۵۶۷۸۹  U+06F0 … U+06F9  — معمول رسید baam و اپ‌های ایران
  • عربی-هندی:      ٠١٢٣٤٥٦٧٨٩  U+0660 … U+0669  — گاهی OCR/Tesseract

برای پارس مبلغ هر دو به 0-9 لاتین نرمال می‌شوند؛ برای OCR هر دو در whitelist لازم است.
"""

from __future__ import annotations

import re

# فارسی — ایران
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
# عربی-هندی — نه همان فارسی
ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ALL_IRANIAN_DIGITS = PERSIAN_DIGITS + ARABIC_INDIC_DIGITS

# در regex: لاتین + هر دو مجموعه
DIGIT_CHAR_CLASS = r"0-9\u06f0-\u06f9\u0660-\u0669"
ZERO_CHARS = "0۰٠"

_TO_ASCII_DIGITS = str.maketrans(
    ALL_IRANIAN_DIGITS,
    "01234567890123456789",
)


def normalize_digits(text: str) -> str:
    """هر دو نوع رقم → 0-9 لاتین (فقط تبدیل ارقام، بدون جداکننده)."""
    return (text or "").translate(_TO_ASCII_DIGITS)


def digits_only_ascii(text: str) -> str:
    """فقط رقم لاتین بعد از نرمال فارسی+عربی."""
    return "".join(c for c in normalize_digits(text) if c.isdigit())


def is_iranian_digit_char(ch: str) -> bool:
    return len(ch) == 1 and (ch in ALL_IRANIAN_DIGITS or (ch.isascii() and ch.isdigit()))


# مبلغ با ویرگول: ۲۸۷,۶۲۵,۰۰۰ یا 287,625,000
IRAN_COMMA_AMOUNT_RE = re.compile(
    rf"(?<!\d)([\u06f0-\u06f9\u0660-\u0669]{{1,3}}(?:[،,]\s*[\u06f0-\u06f9\u0660-\u0669]{{3}}){{2}})(?!\d)"
)
IRAN_COMMA_RIAL_RE = re.compile(
    rf"([\u06f0-\u06f9\u0660-\u0669]{{1,3}}(?:[،,]\s*[\u06f0-\u06f9\u0660-\u0669]{{3}}){{2}})\s*ریال",
    re.IGNORECASE,
)
OCR_ZERO_RIAL_RE = re.compile(
    rf"مبلغ\s*[{ZERO_CHARS}]\s*ریال",
    re.IGNORECASE,
)

# Tesseract whitelist: لاتین + فارسی + عربی-هندی
TESSERACT_DIGIT_WHITELIST = f"0123456789{PERSIAN_DIGITS}{ARABIC_INDIC_DIGITS}"
