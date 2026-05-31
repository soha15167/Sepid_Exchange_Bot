"""
utils/receipt_ocr.py — OCR helper for Persian receipts.

Needs: Pillow, pytesseract, tesseract-ocr, tesseract-ocr-fas
"""

from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

_MAX_OCR_SIDE = 1400
_MAX_OCR_ATTEMPTS = 10
_PER_CALL_TIMEOUT = 14
_TOTAL_BUDGET_SEC = 62

from utils.iran_digits import (
    IRAN_COMMA_RIAL_RE,
    TESSERACT_DIGIT_WHITELIST,
    normalize_digits,
)

_RECEIPT_HINTS = re.compile(
    r"ریال|مبلغ|رسید|حواله|بانک|پایا|پیگیری|خرداد|فروردین|انتقال|موفق",
    re.IGNORECASE,
)
_BAD_AMOUNT_CTX = re.compile(
    r"پیگیری|شماره\s*پی|شبا|شماره|حساب|مبدا|IR\d",
    re.IGNORECASE,
)
_COMMA_AMOUNT = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3}){2})(?!\d)")
_SPACED_RIAL = re.compile(
    rf"([\d\u06f0-\u06f9\u0660-\u0669][\d\u06f0-\u06f9\u0660-\u0669\s,،٬]{{6,36}})\s*ریال",
    re.IGNORECASE,
)
_MIN_TRANSFER_RIAL = 10_000_000


def _normalize_for_amount(text: str) -> str:
    s = normalize_digits(text or "")
    for ch in "،٬﹐":
        s = s.replace(ch, ",")
    return s


def _amounts_from_digit_soup(text: str, *, require_rial: bool = True) -> list[int]:
    """فقط روی متن کوتاه نوار مبلغ — نه کل رسید."""
    src = text or ""
    if require_rial and not re.search(r"ریال|مبلغ", src, re.I):
        return []
    if len(src) > 400:
        return []
    digits = re.sub(r"\D", "", _normalize_for_amount(src))
    if len(digits) < 9:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for width in (9, 10):
        for i in range(0, len(digits) - width + 1):
            chunk = digits[i : i + width]
            if chunk[0] == "0":
                continue
            try:
                v = int(chunk)
            except ValueError:
                continue
            if v < _MIN_TRANSFER_RIAL or v in seen or v % 1000 != 0:
                continue
            seen.add(v)
            out.append(v)
    return out


def _comma_groups(token: str) -> int:
    return len(re.findall(r",\s*\d{3}", token or ""))


def is_confident_transfer_amount(value: int, token: str = "") -> bool:
    v = int(value)
    if v < _MIN_TRANSFER_RIAL or v % 1000 != 0:
        return False
    tok = token or f"{v:,}"
    if _comma_groups(tok) >= 2:
        return True
    return len(str(v)) <= 10 and v >= 100_000_000


def amount_candidates_from_text(text: str) -> list[int]:
    """مبالغ واقعی حواله — نه شماره پیگیری ۱۴+ رقمی."""
    raw = text or ""
    t = _normalize_for_amount(raw)
    found: list[int] = []
    seen: set[int] = set()

    for m in IRAN_COMMA_RIAL_RE.finditer(raw):
        token = _normalize_for_amount(m.group(1))
        try:
            v = int(token.replace(",", ""))
        except ValueError:
            continue
        if v < _MIN_TRANSFER_RIAL or v in seen:
            continue
        seen.add(v)
        found.append(v)

    for m in _SPACED_RIAL.finditer(t):
        blob = re.sub(r"\D", "", m.group(1))
        if len(blob) < 9:
            continue
        for width in (11, 10, 9):
            if len(blob) < width:
                continue
            try:
                v = int(blob[:width])
            except ValueError:
                continue
            if v < _MIN_TRANSFER_RIAL or v in seen:
                continue
            seen.add(v)
            found.append(v)

    for m in _COMMA_AMOUNT.finditer(t):
        token = m.group(1)
        try:
            v = int(token.replace(",", ""))
        except ValueError:
            continue
        if v < _MIN_TRANSFER_RIAL or v in seen:
            continue
        ctx = t[max(0, m.start() - 40) : m.end() + 40]
        if _BAD_AMOUNT_CTX.search(ctx) and not re.search(
            r"مبلغ|ریال|انتقال\s*پول", ctx, re.I
        ):
            continue
        seen.add(v)
        found.append(v)

    for m in re.finditer(r"(?<!\d)(\d{9,11})(?!\d)", t):
        if m.end() < len(t) and t[m.end()].isdigit():
            continue
        try:
            v = int(m.group(1))
        except ValueError:
            continue
        if v < 100_000_000 or v in seen:
            continue
        ctx = t[max(0, m.start() - 35) : m.end() + 35]
        if not re.search(r"مبلغ|ریال", ctx, re.I):
            continue
        if _BAD_AMOUNT_CTX.search(ctx):
            continue
        seen.add(v)
        found.append(v)

    return found


def best_amount_from_text(text: str) -> int:
    """ترجیح مبلغ با ویرگول معتبر — نه max عددی."""
    t = text or ""
    scored: list[tuple[int, int]] = []
    for m in _COMMA_AMOUNT.finditer(_normalize_for_amount(t)):
        token = m.group(1)
        try:
            v = int(token.replace(",", ""))
        except ValueError:
            continue
        if not is_confident_transfer_amount(v, token):
            continue
        ctx = t[max(0, m.start() - 30) : m.end() + 30]
        sc = 100
        if re.search(r"مبلغ|ریال|انتقال\s*پول", ctx, re.I):
            sc += 80
        if _BAD_AMOUNT_CTX.search(ctx):
            sc -= 90
        scored.append((v, sc))
    for v in _amounts_from_digit_soup(t, require_rial=True):
        if is_confident_transfer_amount(v):
            scored.append((v, 60))
    if not scored:
        return 0
    scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return scored[0][0] if scored[0][1] >= 50 else 0


def text_has_parseable_amount(text: str) -> bool:
    return bool(amount_candidates_from_text(text))


def ocr_text_quality_score(text: str) -> float:
    if not text:
        return 0.0
    s = text.strip()
    n = len(s)
    if n == 0:
        return 0.0
    persian = sum(1 for c in s if "\u0600" <= c <= "\u06FF")
    latin = sum(1 for c in s if c.isascii() and c.isalpha())
    score = (persian / n) * 4.0 - (latin / n) * 2.0
    if amount_candidates_from_text(s):
        score += 2.5
    if _RECEIPT_HINTS.search(s):
        score += 0.8
    return score


def ocr_text_looks_like_receipt(text: str) -> bool:
    return text_has_parseable_amount(text)


def _tesseract_langs() -> set[str]:
    try:
        import pytesseract  # type: ignore

        return set(pytesseract.get_languages(config="") or [])
    except Exception:
        return set()


def _fit_for_ocr(gray, max_side: int = _MAX_OCR_SIDE) -> object:
    from PIL import Image  # type: ignore

    w, h = gray.size
    longest = max(w, h)
    if longest > max_side:
        s = max_side / longest
        gray = gray.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)
    elif longest < 720:
        s = min(1.6, 960 / longest)
        gray = gray.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)
    return gray


def _upscale(im, factor: float = 2.2):
    from PIL import Image  # type: ignore

    w, h = im.size
    return im.resize(
        (max(1, int(w * factor)), max(1, int(h * factor))),
        Image.Resampling.LANCZOS,
    )


def _crop_rel(im, x0: float, y0: float, x1: float, y1: float):
    w, h = im.size
    return im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def _open_gray(image_path: str):
    from PIL import Image, ImageOps  # type: ignore

    rgb = Image.open(image_path).convert("RGB")
    return ImageOps.autocontrast(rgb.convert("L"))


def _enhance_band(im, *, strong: bool = False) -> object:
    from PIL import ImageEnhance, ImageFilter  # type: ignore

    im = im.filter(ImageFilter.MedianFilter(size=3))
    c = 2.6 if strong else 2.2
    return ImageEnhance.Contrast(im).enhance(c).filter(ImageFilter.SHARPEN)


def _ocr_langs() -> str:
    available = _tesseract_langs()
    parts = [x for x in ("fas", "eng") if x in available]
    if len(parts) == 2:
        return "fas+eng"
    return parts[0] if parts else "eng"


def _parse_comma_amounts_from_ocr_text(txt: str) -> list[int]:
    norm = _normalize_for_amount(txt or "")
    out: list[int] = []
    for m in _COMMA_AMOUNT.finditer(norm):
        try:
            v = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if is_confident_transfer_amount(v, m.group(1)):
            out.append(v)
    return out


def _ocr_data_text(im, lang: str, config: str) -> str:
    """متن با اطمینان بالاتر از image_to_data."""
    try:
        import pytesseract  # type: ignore
        from pytesseract import Output  # type: ignore

        data = pytesseract.image_to_data(
            im,
            lang=lang,
            config=config,
            output_type=Output.DICT,
            timeout=_PER_CALL_TIMEOUT,
        )
    except Exception:
        return ""
    parts: list[str] = []
    for i, conf in enumerate(data.get("conf") or []):
        try:
            c = int(float(conf))
        except (TypeError, ValueError):
            continue
        if c < 35:
            continue
        t = (data.get("text") or [""])[i].strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _full_page_amount_ocr(image_path: str, deadline: float, lang: str) -> int:
    """رسید لیستی (صادرات و …) — OCR کل صفحه برای «مبلغ حواله»."""
    if time.monotonic() >= deadline:
        return 0
    try:
        gray = _open_gray(image_path)
    except Exception:
        return 0
    base = _enhance_band(gray, strong=True)
    big = _upscale(base, 2.2)
    best = 0
    for cfg in ("--psm 6", "--psm 4", "--psm 11"):
        if time.monotonic() >= deadline:
            break
        txt = _run_ocr(big, lang, cfg)
        if not txt:
            continue
        for v in _parse_comma_amounts_from_ocr_text(txt):
            if v > best:
                best = v
        b = best_amount_from_text(txt)
        if b and b > best:
            best = b
        if best and is_confident_transfer_amount(best):
            logger.info("receipt_ocr: full_page amount=%s", best)
            return best
    return best


def _baam_amount_ocr(image_path: str, deadline: float) -> int:
    """
    اسکرین‌شات baam: خط «مبلغ | ۲۸۷,۶۲۵,۰۰۰ ریال» (عدد سمت چپ در RTL).
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps  # type: ignore
    except Exception:
        return 0

    try:
        rgb = Image.open(image_path).convert("RGB")
    except Exception:
        return 0

    w, h = rgb.size
    lang = _ocr_langs()
    # (x0,y0,x1,y1) — نوار مبلغ + فقط ارقام چپ
    crops = (
        (0.04, 0.16, 0.78, 0.27, "digits_left"),
        (0.06, 0.14, 0.94, 0.30, "amount_row"),
        (0.08, 0.10, 0.92, 0.34, "amount_block"),
        # Blu / رسید تیره — مبلغ درشت وسط صفحه
        (0.05, 0.18, 0.95, 0.50, "blu_large_amt"),
        (0.08, 0.22, 0.92, 0.42, "blu_amt_tight"),
    )
    best = 0
    cfgs = (
        f"--psm 7 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬ریال ",
        f"--psm 6 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬",
        "--psm 7",
        "--psm 6",
    )
    for x0, y0, x1, y1, tag in crops:
        if time.monotonic() >= deadline:
            break
        band = rgb.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
        gray = ImageOps.autocontrast(band.convert("L"))
        for scale in (3.5, 4.5):
            if time.monotonic() >= deadline:
                break
            big = _upscale(gray, scale)
            for im in (
                big,
                ImageEnhance.Contrast(big).enhance(2.8),
                big.point(lambda p, t=168: 255 if p > t else 0),
                ImageOps.invert(big.point(lambda p, t=168: 255 if p > t else 0)),
            ):
                if time.monotonic() >= deadline:
                    break
                for cfg in cfgs:
                    for txt in (
                        _run_ocr(im, lang, cfg),
                        _ocr_data_text(im, lang, cfg),
                    ):
                        if not txt:
                            continue
                        for v in _parse_comma_amounts_from_ocr_text(txt):
                            if v > best:
                                best = v
                                logger.info(
                                    "receipt_ocr: baam %s amount=%s sample=%r",
                                    tag,
                                    v,
                                    txt[:70].replace("\n", " "),
                                )
                            if is_confident_transfer_amount(v):
                                return v
                        # ارقام چسبیده: 287625000
                        blob = _normalize_for_amount(txt)
                        for m in re.finditer(r"(?<!\d)(\d{9})(?!\d)", blob):
                            v = int(m.group(1))
                            if is_confident_transfer_amount(v):
                                logger.info(
                                    "receipt_ocr: baam %s compact amount=%s",
                                    tag,
                                    v,
                                )
                                return v
    if not best:
        logger.warning("receipt_ocr: baam amount not found in image")
    return best


def _amount_region_crops(image_path: str) -> list[tuple[str, object]]:
    """
    نوار مبلغ — رسید baam (اسکرین‌شات) و عکس از LCD.
    """
    gray = _open_gray(image_path)
    w, h = gray.size
    bands_def = (
        # baam: خط مبلغ زیر «انتقال پول موفق»
        ("baam_amt_line", 0.10, 0.17, 0.90, 0.27, True),
        ("baam_amt", 0.12, 0.14, 0.88, 0.32, True),
        ("baam_amt2", 0.08, 0.16, 0.92, 0.38, True),
        ("blu_amt", 0.06, 0.18, 0.94, 0.52, True),
        ("blu_amt2", 0.10, 0.24, 0.90, 0.44, True),
        # عکس از صفحهٔ گوشی
        ("phone_amt", 0.12, 0.18, 0.88, 0.48, False),
        ("phone_amt_hi", 0.08, 0.12, 0.92, 0.42, False),
    )
    out: list[tuple[str, object]] = []
    for tag, x0, y0, x1, y1, strong in bands_def:
        band = _crop_rel(gray, x0, y0, x1, y1)
        if band.size[0] < 60 or band.size[1] < 30:
            continue
        base = _enhance_band(band, strong=strong)
        big = _upscale(base, 2.5 if strong else 2.0)
        out.append((f"{tag}_big", big))
        out.append(
            (
                f"{tag}_bw",
                big.point(lambda p, t=155: 255 if p > t else 0),
            )
        )
    return out


def _prepare_variants(image_path: str) -> list[tuple[str, object]]:
    from PIL import ImageEnhance, ImageFilter, ImageOps  # type: ignore

    gray = _open_gray(image_path)
    inner = _fit_for_ocr(_crop_rel(gray, 0.08, 0.06, 0.92, 0.94))
    out: list[tuple[str, object]] = []
    for tag, im in (
        ("inner_sharp", ImageEnhance.Contrast(inner).enhance(2.0).filter(ImageFilter.SHARPEN)),
        ("inner_bw", None),
    ):
        if tag == "inner_bw":
            im = ImageEnhance.Contrast(inner).enhance(2.0).point(lambda p: 255 if p > 145 else 0)
        out.append((tag, im))
    inv = ImageOps.autocontrast(ImageOps.invert(inner))
    out.append(("inner_inv", inv))
    return out


def _run_ocr(img, lang: str, config: str) -> str:
    import pytesseract  # type: ignore

    try:
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


def _ocr_amount_regions(
    image_path: str, deadline: float, lang: str = "fas"
) -> str:
    """OCR ناحیهٔ مبلغ با بزرگ‌نمایی و فقط ارقام."""
    cfgs = (
        f"--psm 7 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬ریالمبلغ ",
        f"--psm 8 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬",
        f"--psm 13 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬",
        "--psm 6",
        "--psm 11",
    )
    parts: list[str] = []
    for tag, band in _amount_region_crops(image_path):
        if time.monotonic() >= deadline:
            break
        for cfg in cfgs:
            txt = _run_ocr(band, lang, cfg)
            if not txt:
                continue
            parts.append(txt)
            best = best_amount_from_text(txt)
            if best and is_confident_transfer_amount(best):
                logger.info(
                    "receipt_ocr: amount band %s ok amount=%s sample=%r",
                    tag,
                    best,
                    txt[:80].replace("\n", " "),
                )
                return "\n".join(parts)
    return "\n".join(parts)


def ocr_best_amount_from_image(image_path: str, *, budget_sec: float = 24) -> int:
    """فقط مبلغ را از نوار بالای رسید (baam و …) استخراج می‌کند."""
    if not image_path or not os.path.exists(image_path):
        return 0
    deadline = time.monotonic() + budget_sec
    baam = _baam_amount_ocr(image_path, deadline)
    if baam:
        return baam
    if time.monotonic() >= deadline:
        return 0

    available = _tesseract_langs()
    lang = "fas" if "fas" in available else "eng"
    full = _full_page_amount_ocr(image_path, deadline, lang)
    if full:
        return full
    if time.monotonic() >= deadline:
        return 0
    texts: list[str] = []

    amt_txt = _ocr_amount_regions(image_path, deadline, lang)
    if amt_txt:
        texts.append(amt_txt)

    cfgs = (
        f"--psm 7 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬",
        f"--psm 8 -c tessedit_char_whitelist={TESSERACT_DIGIT_WHITELIST},،٬",
        "--psm 6",
    )
    for tag, band in _amount_region_crops(image_path):
        if time.monotonic() >= deadline:
            break
        big = band
        for cfg in cfgs:
            txt = _run_ocr(big, lang, cfg)
            if txt:
                texts.append(txt)

    combined = "\n".join(texts)
    best = best_amount_from_text(combined)
    if best:
        logger.info("receipt_ocr: image amount=%s", best)
    return best


def ocr_image_to_text(image_path: str, *, quick: bool = False) -> tuple[bool, str]:
    """
    quick=True وقتی Vision همزمان فعال است — فقط baam + یک پاس سبک (زیر ~۴۰ث).
    """
    t0 = time.monotonic()
    deadline = t0 + (40.0 if quick else _TOTAL_BUDGET_SEC)
    max_attempts = 4 if quick else _MAX_OCR_ATTEMPTS

    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except Exception:
        logger.warning("receipt_ocr: Pillow not installed")
        return False, ""

    if not image_path or not os.path.exists(image_path):
        return False, ""

    available = _tesseract_langs()
    lang = "fas" if "fas" in available else "eng"
    if "fas" not in available:
        logger.warning("receipt_ocr: install tesseract-ocr-fas")

    chunks: list[str] = []
    tried = 0

    baam_amt = _baam_amount_ocr(image_path, deadline)
    if baam_amt:
        chunks.append(f"مبلغ\n{baam_amt:,} ریال")
    elif time.monotonic() < deadline:
        fp_amt = _full_page_amount_ocr(image_path, deadline, lang)
        if fp_amt:
            chunks.append(f"مبلغ حواله\n{fp_amt:,} ریال")

    # ۱) ناحیهٔ مبلغ (رسید baam)
    amt_txt = _ocr_amount_regions(image_path, deadline, lang)
    tried += 3
    if amt_txt:
        chunks.append(amt_txt)

    if not text_has_parseable_amount("\n".join(chunks)):
        variants = _prepare_variants(image_path)
        if quick:
            variants = variants[:1]
        for tag, img in variants:
            if time.monotonic() >= deadline or tried >= max_attempts:
                break
            cfgs = ("--psm 6",) if quick else ("--psm 6", "--psm 4")
            for cfg in cfgs:
                if tried >= max_attempts:
                    break
                tried += 1
                txt = _run_ocr(img, lang, cfg)
                if txt:
                    chunks.append(txt)
                if text_has_parseable_amount("\n".join(chunks)):
                    break
            if text_has_parseable_amount("\n".join(chunks)):
                break

    if not quick and not text_has_parseable_amount("\n".join(chunks)):
        extra = _ocr_amount_regions(image_path, deadline, lang)
        if extra:
            chunks.append(extra)

    combined = "\n\n".join(c for c in chunks if c)

    cands = amount_candidates_from_text(combined)
    has_amt = bool(cands)
    elapsed = time.monotonic() - t0
    logger.info(
        "receipt_ocr: %.1fs tried~=%s has_amount=%s amounts=%s chars=%s",
        elapsed,
        tried,
        has_amt,
        cands[:3],
        len(combined),
    )
    if combined:
        return True, combined
    return False, ""
