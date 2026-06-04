"""استخراج عمومی از متن OCR — بدون قالب ثابت بانک."""

from __future__ import annotations

import re

from banking_recognition.banks.database import (
    detect_bank_from_card,
    detect_bank_from_sheba,
    detect_bank_from_text,
)
from utils.iran_digits import normalize_digits


def _norm(text: str) -> str:
    return normalize_digits(text or "")


def extract_card_numbers(text: str) -> list[str]:
    raw = _norm(text)
    found: list[str] = []
    for m in re.finditer(r"\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b", raw):
        d = re.sub(r"\D", "", m.group(1))
        if len(d) == 16 and d not in found:
            found.append(d)
    compact = re.sub(r"\D", "", raw)
    for m in re.finditer(r"(\d{16})", compact):
        if m.group(1) not in found:
            found.append(m.group(1))
    return found


def extract_sheba(text: str) -> str:
    raw = _norm(text).upper()
    m = re.search(r"IR\s*[\d\s]{22,30}", raw, re.I)
    if m:
        s = re.sub(r"\s+", "", m.group(0).upper())
        if s.startswith("IR") and len(s) >= 26:
            return s[:26]
    compact = re.sub(r"[^A-Z0-9]", "", raw)
    ix = compact.find("IR")
    if ix >= 0 and len(compact) >= ix + 26:
        return compact[ix : ix + 26]
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 24:
        return "IR" + digits[:24]
    return ""


def extract_amount_rial(text: str) -> int | None:
    raw = _norm(text)
    best = 0
    for m in re.finditer(
        r"(?:مبلغ|ریال|amount)[^\d]{0,30}([\d,،٬]{4,})",
        raw,
        re.I,
    ):
        token = m.group(1).replace(",", "").replace("،", "").replace("٬", "")
        try:
            v = int(token)
        except ValueError:
            continue
        if v > best:
            best = v
    if best >= 10_000:
        return best
    for m in re.finditer(r"([\d,،٬]{7,})", raw):
        token = m.group(1).replace(",", "").replace("،", "").replace("٬", "")
        try:
            v = int(token)
        except ValueError:
            continue
        if 10_000 <= v <= 50_000_000_000 and v > best:
            best = v
    return best if best >= 10_000 else None


def extract_tracking(text: str) -> str:
    raw = _norm(text)
    for pat in (
        r"شماره\s*پیگیری[:\s]*(\d{6,})",
        r"پیگیری[:\s]*(\d{6,})",
        r"کد\s*پیگیری[:\s]*(\d{6,})",
        r"reference[:\s]*(\d{6,})",
    ):
        m = re.search(pat, raw, re.I)
        if m:
            return m.group(1)
    return ""


def extract_jdate(text: str) -> str:
    raw = _norm(text)
    m = re.search(r"(13\d{2}|14\d{2})[/.\\-](\d{1,2})[/.\\-](\d{1,2})", raw)
    if m:
        return f"{int(m.group(1)):04d}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
    return ""


def extract_time(text: str) -> str:
    raw = _norm(text)
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        sec = m.group(3) or "00"
        return f"{h:02d}:{mi:02d}:{sec}"
    return ""


def extract_names(text: str) -> dict[str, str]:
    raw = text or ""
    out = {"owner_name": "", "sender_name": "", "receiver_name": ""}
    patterns = [
        ("receiver_name", r"(?:به|گیرنده|متعلق\s*به|صاحب\s*حساب)[:\s]*([^\n]{3,60})"),
        ("sender_name", r"(?:از|فرستنده|واریز\s*کننده|انتقال\s*دهنده)[:\s]*([^\n]{3,60})"),
        ("owner_name", r"(?:نام\s*صاحب|نام\s*دارنده|نام)[:\s]*([^\n]{3,60})"),
    ]
    for key, pat in patterns:
        m = re.search(pat, raw, re.I)
        if m:
            val = m.group(1).strip()
            if len(val) >= 3:
                out[key] = val[:80]
    return out


def extract_status(text: str) -> str:
    low = (text or "").lower()
    if any(x in low for x in ("موفق", "انجام شد", "successful", "تایید")):
        return "موفق"
    if any(x in low for x in ("ناموفق", "رد", "failed", "لغو")):
        return "ناموفق"
    return ""


def extract_all_fields(text: str) -> dict:
    cards = extract_card_numbers(text)
    sheba = extract_sheba(text)
    card = cards[0] if cards else ""
    bank = (
        detect_bank_from_text(text)
        or detect_bank_from_card(card)
        or detect_bank_from_sheba(sheba)
    )
    names = extract_names(text)
    try:
        from utils.card_account_ocr import parse_account_from_ocr

        acct = parse_account_from_ocr(text)
        if acct.get("name") and not names["owner_name"]:
            names["owner_name"] = acct["name"]
        if acct.get("card") and not card:
            card = re.sub(r"\D", "", acct["card"])
        if acct.get("iban") and not sheba:
            sheba = acct["iban"]
    except Exception:
        pass

    return {
        "card_number": card,
        "sheba": sheba,
        "account_number": "",
        "bank_name": bank,
        "amount": extract_amount_rial(text),
        "date": extract_jdate(text),
        "time": extract_time(text),
        "tracking_number": extract_tracking(text),
        "transaction_id": extract_tracking(text),
        "status": extract_status(text),
        **names,
    }
