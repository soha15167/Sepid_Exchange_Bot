"""
utils/card_account_ocr.py — OCR عکس کارت/حساب بانکی برای جمع‌آوری حساب در معامله.

از روی کارت باید خوانده شود: شماره کارت، شماره شبا، نام و نام خانوادگی.
"""

from __future__ import annotations

import logging
import os
import re
import time

from utils.iran_digits import digits_only_ascii, normalize_digits

logger = logging.getLogger(__name__)

_PER_CALL_TIMEOUT = 12
_TOTAL_BUDGET_SEC = 32

_CARD_BIN_TO_BANK: dict[str, str] = {
    "603799": "ملی",
    "589210": "سپه",
    "627648": "توسعه صادرات",
    "627961": "صنعت و معدن",
    "603770": "کشاورزی",
    "603701": "کشاورزی",
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
    r"بانک|شبا|شماره|کارت|حساب|IR\b|account|iban|card|visa|master|"
    r"mehr\s*gostar|مهرگستر|کشاورزی|ملت|ملی|سپه|پاسار|english|farsi",
    re.I,
)
_PERSIAN_LINE = re.compile(r"[\u0600-\u06FF]{2,}")
_LATIN_NAME_LINE = re.compile(r"^[A-Za-z][A-Za-z\s'\-]{2,38}[A-Za-z]$")
_DIGIT_WHITELIST = "0123456789IR "
_KNOWN_CARD_PREFIXES = (
    "6037",
    "6219",
    "5022",
    "6104",
    "6273",
    "5892",
    "6362",
    "5054",
    "6393",
    "6063",
    "6037",
)
# شروع ۴رقمی که شبا است نه کارت
_IBAN_GROUP_STARTS = frozenset({"5901", "5891", "6000", "0000", "0001", "5157", "5919"})


def _bank_from_card(card_digits: str) -> str:
    d = digits_only_ascii(card_digits)
    if len(d) < 6:
        return ""
    return _CARD_BIN_TO_BANK.get(d[:6], "")


def _format_card_groups(card: str) -> str:
    d = digits_only_ascii(card)
    if len(d) != 16:
        return (card or "").strip()
    return f"{d[0:4]} {d[4:8]} {d[8:12]} {d[12:16]}"


def _extract_iban(raw: str) -> str:
    t = normalize_digits(raw or "").upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    t = t.replace("1R", "IR").replace("LR", "IR").replace("IRN", "IR")
    m = re.search(r"IR\d{24}", t)
    if m:
        return m.group(0)
    m = re.search(r"IR\d{22,26}", t)
    if m:
        s = m.group(0)
        if len(s) >= 26:
            return s[:26]
    digits = re.sub(r"\D", "", t)
    if len(digits) >= 24:
        for i in range(0, len(digits) - 23):
            chunk = digits[i : i + 24]
            if chunk.startswith("59") or chunk.startswith("18"):
                return f"IR{chunk}"
    # OCR گاهی IR را جدا می‌نویسد
    m2 = re.search(
        r"IR[\s\-]*(\d{4}[\s\-]+\d{4}[\s\-]+\d{4}[\s\-]+\d{4}[\s\-]+\d{4}[\s\-]+\d{4})",
        raw or "",
        re.I,
    )
    if m2:
        body = digits_only_ascii(m2.group(1))
        if len(body) >= 24:
            return f"IR{body[:24]}"
    return ""


def _looks_like_card16(d: str) -> bool:
    if len(d) != 16 or d[0] == "0":
        return False
    if d[:4] in _IBAN_GROUP_STARTS or d.startswith("5901"):
        return False
    if _bank_from_card(d):
        return True
    return any(d.startswith(p) for p in _KNOWN_CARD_PREFIXES)


def _card_overlaps_iban(card: str, iban: str) -> bool:
    if not card or not iban:
        return False
    iban_digits = iban[2:] if iban.upper().startswith("IR") else digits_only_ascii(iban)
    if card in iban_digits or card in iban.upper():
        return True
    return False


def _digits_from_spaced_groups(match_text: str) -> str:
    mt = (match_text or "").strip()
    m = re.fullmatch(r"(\d{4})\s+(\d{4})\s+(\d{4})\s+(\d{4})", mt)
    if m:
        return "".join(m.groups())
    parts = re.findall(r"\d{4}", mt)
    if len(parts) == 4:
        return "".join(parts)
    return digits_only_ascii(mt)


def _score_card_candidate(card: str, src: str, iban: str) -> tuple[int, int, int]:
    spaced = bool(
        re.search(
            rf"(?<!\d){card[:4]}\s+{card[4:8]}\s+{card[8:12]}\s+{card[12:16]}(?!\d)",
            src,
        )
    )
    known_bin = 2 if _bank_from_card(card) else 0
    prefix_bonus = 1 if any(card.startswith(p) for p in _KNOWN_CARD_PREFIXES) else 0
    iban_penalty = -5 if _card_overlaps_iban(card, iban) else 0
    return (2 if spaced else 0, known_bin + prefix_bonus + iban_penalty, len(card))


def _extract_card(raw: str, *, iban: str = "") -> str:
    src = raw or ""
    candidates: list[str] = []

    def _try_add(d: str) -> None:
        d = digits_only_ascii(d)
        if _looks_like_card16(d) and not _card_overlaps_iban(d, iban) and d not in candidates:
            candidates.append(d)

    for m in re.finditer(r"(?<!\d)(\d{4}\s+\d{4}\s+\d{4}\s+\d{4})(?!\d)", src):
        chunk = m.group(1)
        parts = re.findall(r"\d{4}", chunk)
        if parts and parts[0] in _IBAN_GROUP_STARTS:
            continue
        _try_add(_digits_from_spaced_groups(chunk))

    t = normalize_digits(src)
    for m in re.finditer(
        r"(?<!\d)(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})(?!\d)", t
    ):
        chunk = m.group(1)
        d = (
            _digits_from_spaced_groups(chunk)
            if re.search(r"\s", chunk)
            else digits_only_ascii(chunk)
        )
        _try_add(d)

    for m in re.finditer(r"(?<!\d)(\d{16})(?!\d)", t):
        _try_add(m.group(1))

    soup = digits_only_ascii(t)
    for i in range(0, max(0, len(soup) - 15)):
        _try_add(soup[i : i + 16])

    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=lambda c: _score_card_candidate(c, src, iban))


def _clean_name_line(line: str) -> str:
    line = re.sub(r"\s+", " ", (line or "").strip())
    line = re.sub(r"[^\u0600-\u06FFa-zA-Z\s'\-]", "", line).strip()
    return line


def _name_garbage(line: str) -> bool:
    low = line.lower()
    if any(
        x in low
        for x in (
            "message",
            "forward",
            "telegram",
            "photo",
            "edited",
            "bank kesh",
            "keshavarzi",
            "mehr",
        )
    ):
        return True
    persian = len(re.findall(r"[\u0600-\u06FF]", line))
    latin = len(re.findall(r"[A-Za-z]", line))
    if latin > 0 and persian > 0 and latin >= persian:
        return True
    if persian < 3 and not _LATIN_NAME_LINE.match(line):
        return True
    return False


def _name_score(line: str) -> tuple[int, int]:
    persian_words = [
        w for w in line.split() if re.fullmatch(r"[\u0600-\u06FF]{2,15}", w or "")
    ]
    if len(persian_words) >= 2:
        if len(persian_words) > 4:
            return (0, 0)
        avg_len = sum(len(w) for w in persian_words) / len(persian_words)
        if avg_len < 3.0:
            return (0, 0)
        if persian_words[0] == persian_words[-1]:
            return (0, 0)
        return (4, sum(len(w) for w in persian_words))
    if len(persian_words) == 1 and len(persian_words[0]) >= 5:
        return (2, len(persian_words[0]))
    if _LATIN_NAME_LINE.match(line):
        words = [w for w in line.split() if len(w) >= 3]
        if len(words) >= 2:
            return (3, len(line))
    return (0, 0)


def _extract_name(raw: str) -> str:
    best = ""
    best_score: tuple[int, int] = (0, 0)
    for line in (raw or "").splitlines():
        line = _clean_name_line(line)
        if len(line) < 4 or _SKIP_NAME.search(line) or _name_garbage(line):
            continue
        if re.search(r"\d", line):
            continue
        score = _name_score(line)
        if score > best_score:
            best_score = score
            best = line
    if best_score[0] >= 3:
        return best.strip()
    return ""


def parse_account_from_ocr(raw: str, *, name_raw: str = "") -> dict[str, str]:
    """فیلدهای ساختاریافته از متن OCR."""
    iban = _extract_iban(raw)
    card = _extract_card(raw, iban=iban)
    name = _extract_name(name_raw or raw)
    return {
        "name": name,
        "card": card,
        "iban": iban,
    }


def format_account_text(fields: dict[str, str], *, raw_fallback: str = "") -> str:
    """متن یکپارچه: نام، شماره کارت، شماره شبا."""
    lines: list[str] = []
    if fields.get("name"):
        lines.append(f"نام و نام خانوادگی: {fields['name']}")
    if fields.get("card"):
        lines.append(f"شماره کارت: {_format_card_groups(fields['card'])}")
    if fields.get("iban"):
        lines.append(f"شماره شبا: {fields['iban']}")
    if lines:
        return "\n".join(lines)
    fb = (raw_fallback or "").strip()
    if len(fb) >= 8:
        return fb[:2000]
    return ""


def account_fields_complete(fields: dict[str, str]) -> bool:
    """هر سه فیلد اصلی کارت خوانده شده باشد."""
    return bool(
        (fields.get("name") or "").strip()
        and (fields.get("card") or "").strip()
        and (fields.get("iban") or "").strip()
    )


def _run_tesseract(img, lang: str, config: str) -> str:
    try:
        import pytesseract  # type: ignore

        return (
            pytesseract.image_to_string(
                img,
                lang=lang,
                config=config,
                timeout=_PER_CALL_TIMEOUT,
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _card_crop_variants(image_path: str) -> list[tuple[str, object]]:
    """برش‌های کارت بانکی ایران."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore

    from utils.receipt_ocr import _crop_rel, _fit_for_ocr, _open_gray

    gray = _open_gray(image_path)
    if gray.size[0] < 40 or gray.size[1] < 40:
        return []

    full = _fit_for_ocr(gray, max_side=1800)
    bands = (
        ("full", 0.02, 0.02, 0.98, 0.98),
        ("card_num", 0.04, 0.28, 0.96, 0.60),
        ("card_num2", 0.02, 0.35, 0.98, 0.65),
        ("iban_band", 0.04, 0.50, 0.96, 0.84),
        ("iban_band2", 0.02, 0.55, 0.98, 0.92),
        ("name_band", 0.08, 0.62, 0.92, 0.86),
        ("name_band2", 0.12, 0.68, 0.88, 0.82),
    )
    out: list[tuple[str, object]] = []
    for tag, x0, y0, x1, y1 in bands:
        band = _crop_rel(full, x0, y0, x1, y1)
        if band.size[0] < 50 or band.size[1] < 18:
            continue
        sharp = ImageEnhance.Contrast(band).enhance(2.6).filter(ImageFilter.SHARPEN)
        big = sharp.resize(
            (max(1, sharp.size[0] * 2), max(1, sharp.size[1] * 2)),
            Image.Resampling.LANCZOS,
        )
        out.append((f"{tag}_big", big))
        out.append(
            (f"{tag}_bw", big.point(lambda p, t=145: 255 if p > t else 0))
        )
        out.append((f"{tag}_inv", ImageOps.invert(ImageOps.autocontrast(big))))
    return out


def _ocr_bands(
    image_path: str,
    *,
    band_tags: tuple[str, ...],
    lang: str,
    configs: tuple[str, ...],
    deadline: float,
) -> str:
    chunks: list[str] = []
    for tag, img in _card_crop_variants(image_path):
        if time.monotonic() >= deadline:
            break
        if not any(k in tag for k in band_tags):
            continue
        for cfg in configs:
            if time.monotonic() >= deadline:
                break
            txt = _run_tesseract(img, lang, cfg)
            if txt:
                chunks.append(txt)
    return "\n".join(chunks)


def _ocr_card_image_text(image_path: str) -> str:
    """OCR ارقام کارت و شبا."""
    if not image_path or not os.path.exists(image_path):
        return ""
    try:
        from utils.receipt_ocr import _tesseract_langs
    except Exception:
        return ""

    available = _tesseract_langs()
    lang_digits = "eng" if "eng" in available else "fas"
    deadline = time.monotonic() + _TOTAL_BUDGET_SEC
    digit_cfgs = (
        f"--psm 7 -c tessedit_char_whitelist={_DIGIT_WHITELIST}",
        f"--psm 6 -c tessedit_char_whitelist={_DIGIT_WHITELIST}",
        f"--psm 11 -c tessedit_char_whitelist={_DIGIT_WHITELIST}",
        "--psm 6",
    )
    raw = _ocr_bands(
        image_path,
        band_tags=("card_num", "iban", "full"),
        lang=lang_digits,
        configs=digit_cfgs,
        deadline=deadline,
    )
    logger.info(
        "card_account_ocr: digits chars=%s card=%s iban=%s",
        len(raw),
        bool(_extract_card(raw, iban=_extract_iban(raw))),
        bool(_extract_iban(raw)),
    )
    return raw


def _ocr_name_from_image(image_path: str) -> str:
    """OCR ناحیهٔ نام روی کارت (فارسی/لاتین)."""
    if not image_path or not os.path.exists(image_path):
        return ""
    try:
        from utils.receipt_ocr import _tesseract_langs
    except Exception:
        return ""

    available = _tesseract_langs()
    lang = "+".join(x for x in ("fas", "eng") if x in available) or "eng"
    deadline = time.monotonic() + _TOTAL_BUDGET_SEC
    text_cfgs = ("--psm 7", "--psm 6", "--psm 11")
    raw = _ocr_bands(
        image_path,
        band_tags=("name_band",),
        lang=lang,
        configs=text_cfgs,
        deadline=deadline,
    )
    logger.info(
        "card_account_ocr: name chars=%s found=%s",
        len(raw),
        bool(_extract_name(raw)),
    )
    return raw


def ocr_account_from_image(image_path: str) -> tuple[str, str, dict[str, str]]:
    """
    OCR عکس کارت/حساب.
    برمی‌گرداند: (متن فرمت‌شده, متن خام OCR, فیلدهای parsed)
    """
    raw_digits = _ocr_card_image_text(image_path)
    raw_name = _ocr_name_from_image(image_path)
    raw = "\n".join(x for x in (raw_digits, raw_name) if x).strip()

    if not raw or (not _extract_card(raw, iban=_extract_iban(raw)) and not _extract_iban(raw)):
        try:
            from utils.receipt_ocr import ocr_image_to_text

            _ok, extra = ocr_image_to_text(image_path, quick=True)
            extra = (extra or "").strip()
            if extra:
                raw = f"{raw}\n\n{extra}".strip() if raw else extra
        except Exception:
            logger.exception("card_account_ocr: receipt fallback failed")

    raw = (raw or "").strip()
    if not raw:
        return "", "", {}

    fields = parse_account_from_ocr(raw, name_raw=f"{raw_name}\n{raw}")
    formatted = format_account_text(fields, raw_fallback=raw)
    logger.info(
        "card_account_ocr: ok=%s name=%s card=%s iban=%s complete=%s",
        bool(formatted),
        bool(fields.get("name")),
        bool(fields.get("card")),
        bool(fields.get("iban")),
        account_fields_complete(fields),
    )
    return formatted, raw, fields
