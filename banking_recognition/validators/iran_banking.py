"""اعتبارسنجی کارت، شبا، مبلغ، تاریخ."""

from __future__ import annotations

import re
from datetime import datetime

from banking_recognition.banks.database import (
    detect_bank_from_card,
    detect_bank_from_sheba,
)


def luhn_check(card_number: str) -> bool:
    digits = re.sub(r"\D", "", card_number or "")
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def sheba_mod97(sheba: str) -> bool:
    s = re.sub(r"\s+", "", (sheba or "").upper())
    if not s.startswith("IR"):
        s = "IR" + s
    if len(s) != 26:
        return False
    if not re.match(r"^IR\d{24}$", s):
        return False
    rearranged = s[4:] + s[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isdigit():
            numeric += ch
        else:
            numeric += str(ord(ch) - 55)
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def validate_card(card: str) -> tuple[bool, str]:
    d = re.sub(r"\D", "", card or "")
    if len(d) != 16:
        return False, "card_length"
    if not luhn_check(d):
        return False, "luhn"
    return True, ""


def validate_sheba(sheba: str) -> tuple[bool, str]:
    s = re.sub(r"\s+", "", (sheba or "").upper())
    if not s.startswith("IR"):
        s = "IR" + s.replace("IR", "")
    if len(s) != 26:
        return False, "sheba_length"
    if not sheba_mod97(s):
        return False, "sheba_mod97"
    return True, ""


def validate_amount_rial(amount: int | None) -> tuple[bool, str]:
    if amount is None:
        return False, "amount_missing"
    if amount < 10_000:
        return False, "amount_too_small"
    if amount > 50_000_000_000:
        return False, "amount_too_large"
    return True, ""


def validate_jdate(jdate: str) -> tuple[bool, str]:
    s = (jdate or "").strip()
    if not re.match(r"^\d{4}/\d{2}/\d{2}$", s):
        return False, "jdate_format"
    y, m, d = (int(x) for x in s.split("/"))
    if y < 1300 or y > 1500 or m < 1 or m > 12 or d < 1 or d > 31:
        return False, "jdate_range"
    return True, ""


def validate_time(t: str) -> tuple[bool, str]:
    s = (t or "").strip()
    if not s:
        return True, ""
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", s):
        return True, ""
    return False, "time_format"


def cross_validate_fields(
    *,
    card_number: str,
    sheba: str,
    bank_name: str,
    amount: int | None,
) -> list[str]:
    errors: list[str] = []
    if card_number:
        ok, code = validate_card(card_number)
        if not ok:
            errors.append(f"card:{code}")
        bin_bank = detect_bank_from_card(card_number)
        if bank_name and bin_bank and bin_bank != bank_name:
            errors.append("bank_card_mismatch")
    if sheba:
        ok, code = validate_sheba(sheba)
        if not ok:
            errors.append(f"sheba:{code}")
        sheba_bank = detect_bank_from_sheba(sheba)
        if bank_name and sheba_bank and sheba_bank != bank_name:
            errors.append("bank_sheba_mismatch")
    if amount is not None:
        ok, code = validate_amount_rial(amount)
        if not ok:
            errors.append(f"amount:{code}")
    return errors
