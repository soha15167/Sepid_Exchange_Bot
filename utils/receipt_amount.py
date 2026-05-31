"""
utils/receipt_amount.py — نرمال‌سازی مبلغ رسید (ارقام فارسی، صفر اضافه/کم OCR).
"""

from __future__ import annotations

import re

from utils.iran_digits import normalize_digits

_MIN_TRANSFER_RIAL = 5_000_000
_MIN_RECEIPT_RIAL = 1_000_000
_MAX_RIAL = 9_999_999_999_999


def _comma_groups(token: str) -> int:
    return len(re.findall(r",\s*\d{3}", token or ""))


def looks_like_tracking_or_card(value: int) -> bool:
    ds = str(int(value))
    if len(ds) >= 14:
        return True
    if len(ds) >= 11 and ds.startswith("14"):
        return True
    if len(ds) >= 12 and int(value) % 1000 != 0:
        return True
    return False


def is_plausible_transfer_amount(value: int, token: str = "") -> bool:
    v = int(value)
    if v < _MIN_RECEIPT_RIAL or v > _MAX_RIAL:
        return False
    if looks_like_tracking_or_card(v):
        return False
    if v % 1000 != 0:
        return False
    groups = _comma_groups(token)
    digits = len(str(v))
    if groups >= 2:
        return True
    if digits >= 11:
        return False
    return digits >= 7 and v >= _MIN_TRANSFER_RIAL


def _fix_amount_missing_zero(value: int) -> int:
    v = int(value or 0)
    if v < 1_000_000:
        return v
    s = str(v)
    if len(s) == 8 and v % 1000 == 0:
        if is_plausible_transfer_amount(v, s):
            return v
        v10 = v * 10
        if is_plausible_transfer_amount(v10) and len(str(v10)) == 9:
            return v10
    return v


def _fix_amount_extra_zero(value: int) -> int:
    """۵۸۸,۰۰۰,۰۰۰ (OCR/بینایی) → ۵۸,۸۰۰,۰۰۰."""
    v = int(value or 0)
    if v < _MIN_TRANSFER_RIAL:
        return v
    s = str(v)
    for _ in range(3):
        if len(s) >= 9 and v % 10 == 0:
            v_small = v // 10
            if is_plausible_transfer_amount(v_small, str(v_small)):
                v, s = v_small, str(v_small)
                continue
        break
    if len(s) == 10 and v % 10_000 == 0:
        v9 = v // 10
        if is_plausible_transfer_amount(v9):
            return v9
    return v


def normalize_transfer_amount(value: int, token: str = "") -> int:
    tok = normalize_digits(token or "")
    for sep in "،٬﹐":
        tok = tok.replace(sep, ",")
    v = _fix_amount_extra_zero(_fix_amount_missing_zero(int(value or 0)))
    if v >= _MIN_RECEIPT_RIAL and is_plausible_transfer_amount(v, tok or f"{v:,}"):
        return v
    return 0


def parse_rial_amount_text(text: str) -> int:
    s = normalize_digits(text or "")
    for sep in "،٬﹐":
        s = s.replace(sep, ",")
    s = s.replace(",", "").replace(" ", "")
    if not s.isdigit():
        return 0
    return normalize_transfer_amount(int(s))
