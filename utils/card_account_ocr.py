"""
utils/card_account_ocr.py — OCR عکس کارت/حساب بانکی برای جمع‌آوری حساب در معامله.
"""

from __future__ import annotations

import logging
import re

from utils.iran_digits import digits_only_ascii, normalize_digits

logger = logging.getLogger(__name__)

_CARD_BIN_TO_BANK: dict[str, str] = {
    "603799": "ملی",
    "589210": "سپه",
    "627648": "توسعه صادرات",
    "627961": "صنعت و معدن",
    "603770": "کشاورزی",
    "628023": "مسکن",
    "627760": "پست بانک",
    "502908": "توسعه تعاون",
    "627412": "اقتصاد نوین",
    "622106": "پارسیان",
    "502229": "پاسارگاد",
    "627488": "کارآفرین",
    "621986": "سامان",
    "639346": "سینا",
    "639607": "سرمایه",
    "636214": "آینده",
    "502806": "شهر",
    "504706": "شهر",
    "606373": "مهر ایران",
    "627381": "انصار",
    "505785": "ایران زمین",
    "636949": "حکمت",
    "505416": "گردشگری",
    "636795": "مرکزی",
    "610433": "ملت",
    "991975": "ملت",
    "603769": "صادرات",
    "589463": "رفاه",
    "627353": "تجارت",
    "585983": "تجارت",
    "627884": "پارسیان",
    "639370": "مهر اقتصاد",
}

_SKIP_NAME = re.compile(
    r"بانک|شبا|کارت|حساب|IR\d|account|iban|card|visa|master",
    re.I,
)
_PERSIAN_LINE = re.compile(r"[\u0600-\u06FF]{3,}")


def _bank_from_card(card_digits: str) -> str:
    d = digits_only_ascii(card_digits)
    if len(d) < 6:
        return ""
    return _CARD_BIN_TO_BANK.get(d[:6], "")


def _extract_iban(raw: str) -> str:
    t = normalize_digits(raw or "").upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    t = t.replace("1R", "IR").replace("LR", "IR")
    m = re.search(r"IR\d{24}", t)
    if m:
        return m.group(0)
    m = re.search(r"IR\d{22,26}", t)
    if m:
        s = m.group(0)
        if len(s) >= 26:
            return s[:26]
    return ""


def _extract_card(raw: str) -> str:
    t = normalize_digits(raw or "")
    for m in re.finditer(
        r"(?<!\d)(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})(?!\d)", t
    ):
        d = digits_only_ascii(m.group(1))
        if len(d) == 16:
            return d
    for m in re.finditer(r"(?<!\d)(\d{16})(?!\d)", t):
        d = m.group(1)
        if d.startswith("6037") or d.startswith("6219") or d.startswith("5022"):
            return d
        if _bank_from_card(d):
            return d
    return ""


def _extract_name(raw: str) -> str:
    best = ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or _SKIP_NAME.search(line):
            continue
        m = re.search(
            r"(?:نام|name)\s*[:：]?\s*(.+)",
            line,
            re.I,
        )
        if m:
            cand = m.group(1).strip()
            if _PERSIAN_LINE.search(cand) and len(cand) > len(best):
                best = cand
                continue
        if _PERSIAN_LINE.search(line) and not re.search(r"\d{4,}", line):
            if len(line) > len(best):
                best = line
    return best.strip()


def _extract_bank(raw: str, card: str) -> str:
    t = raw or ""
    for pat in (
        r"بانک\s*[:：]?\s*([\u0600-\u06FFA-Za-z\s]{2,30})",
        r"(ملی|ملت|سامان|پاسارگاد|پارسیان|صادرات|تجارت|رفاه|سپه|کشاورزی|مسکن|بلو|آینده|شهر)",
    ):
        m = re.search(pat, t, re.I)
        if m:
            name = m.group(1).strip()
            if name and len(name) <= 30:
                return name
    return _bank_from_card(card)


def parse_account_from_ocr(raw: str) -> dict[str, str]:
    """فیلدهای ساختاریافته از متن OCR."""
    card = _extract_card(raw)
    iban = _extract_iban(raw)
    name = _extract_name(raw)
    bank = _extract_bank(raw, card)
    return {
        "name": name,
        "bank": bank,
        "card": card,
        "iban": iban,
    }


def format_account_text(fields: dict[str, str], *, raw_fallback: str = "") -> str:
    """متن یکپارچه برای ذخیره در دیتابیس."""
    lines: list[str] = []
    if fields.get("name"):
        lines.append(f"نام: {fields['name']}")
    if fields.get("bank"):
        lines.append(f"بانک: {fields['bank']}")
    if fields.get("card"):
        lines.append(f"شماره کارت: {fields['card']}")
    if fields.get("iban"):
        lines.append(f"شبا: {fields['iban']}")
    if lines:
        return "\n".join(lines)
    fb = (raw_fallback or "").strip()
    if len(fb) >= 8:
        return fb[:2000]
    return ""


def ocr_account_from_image(image_path: str) -> tuple[str, str]:
    """
    OCR عکس کارت/حساب.
    برمی‌گرداند: (متن فرمت‌شده برای ذخیره, متن خام OCR)
    """
    from utils.receipt_ocr import ocr_image_to_text

    ok, raw = ocr_image_to_text(image_path, quick=False)
    raw = (raw or "").strip()
    if not ok and not raw:
        return "", ""
    fields = parse_account_from_ocr(raw)
    formatted = format_account_text(fields, raw_fallback=raw)
    logger.info(
        "card_account_ocr: ok=%s name=%s card=%s iban=%s",
        bool(formatted),
        bool(fields.get("name")),
        bool(fields.get("card")),
        bool(fields.get("iban")),
    )
    return formatted, raw
