"""
handlers/iran_panel_sync.py — Admin helper to register in/out transactions in Iran panel.

Workflow:
1) Admin sends /txin or /txout
2) Bot asks admin to send receipt (photo/document) with caption OR a text message containing fields.
3) Bot parses fields and POSTs to panel /transactions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import os
import tempfile

logger = logging.getLogger(__name__)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, IRAN_PANEL_BASE_URL
from utils.iran_digits import (
    OCR_ZERO_RIAL_RE,
    digits_only_ascii,
    normalize_digits,
)
from utils.iran_panel_client import post_transaction
from utils.receipt_amount import normalize_transfer_amount, parse_rial_amount_text
from utils.receipt_ocr import (
    amount_candidates_from_text,
    ocr_best_amount_from_image,
    ocr_image_to_text,
    ocr_text_quality_score,
    text_has_parseable_amount,
)
from utils.receipt_vision import (
    receipt_vision_available,
    receipt_vision_should_run,
    receipt_vision_uses_ollama,
)
from state import user_data_store
from telegram.ext import ApplicationHandlerStop
from utils.telegram_utils import send_or_replace_main_menu

_RTL = "\u200f"
_TX_CANCEL_TEXT = re.compile(
    r"^(?:❌\s*)?(?:انصراف|لغو|"
    r"بازگشت\s*به\s*منوی?\s*اصلی|"
    r"🏠\s*بازگشت(?:\s*به\s*منو(?:ی?\s*اصلی)?)?)$",
    re.IGNORECASE,
)
_KEY = "admin_iran_txn_mode"  # "in" | "out"
_DRAFT_KEY = "admin_iran_txn_draft"  # dict payload
_DRAFT_MODE_KEY = "admin_iran_txn_draft_mode"  # "in" | "out"
_AWAIT_FIELD_KEY = "admin_iran_txn_await_field"  # field name or ""
_DRAFT_MID_KEY = "admin_iran_txn_draft_mid"
_TX_MSG_IDS_KEY = "admin_iran_txn_msg_ids"
_PROMPT_MID_KEY = "admin_iran_txn_prompt_mid"


def _fields_for_mode(mode: str) -> list[str]:
    """فیلدهای قابل ویرایش — ورودی و خروجی سایت متفاوت است."""
    if mode == "in":
        return [
            "iran_amount",
            "jdate",
            "bank_name",
            "depositor_name",
            "transfer_type",
            "description",
        ]
    return [
        "iran_amount",
        "jdate",
        "bank_name",
        "dest_bank",
        "depositor_name",
        "transfer_type",
        "description",
    ]


def is_awaiting_iran_panel_field(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool((context.user_data.get(_AWAIT_FIELD_KEY) or "").strip())


def is_iran_tx_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_awaiting_iran_panel_field(context):
        return True
    if context.user_data.get(_DRAFT_MODE_KEY) in ("in", "out"):
        return True
    return context.user_data.get(_KEY) in ("in", "out")


def _is_tx_cancel_message(text: str) -> bool:
    return bool(_TX_CANCEL_TEXT.match((text or "").strip()))


def _text_looks_like_iran_tx_paste(raw: str) -> bool:
    """متن شبیه فیش/ثبت دستی تراکنش — نه هر پیام تصادفی در فلو tx."""
    t = (raw or "").strip()
    if not t:
        return False
    if re.search(r"مبلغ|تاریخ|ریال|بانک|شبا|حواله|jdate", t, re.I):
        return True
    digits = re.sub(r"\D", "", t)
    return len(digits) >= 7


def _tx_flow_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ انصراف", callback_data="tx|cancel")]]
    )


async def _return_to_main_menu_after_tx_abort(
    bot, chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _cleanup_tx_flow(bot, chat_id, context)
    await send_or_replace_main_menu(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text="🏠 منوی اصلی:",
    )


async def abort_iran_tx_flow_if_active(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """لغو فلو ورودی/خروجی و نمایش منوی اصلی."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return False
    if not is_iran_tx_flow_active(context):
        return False
    chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id
    await _return_to_main_menu_after_tx_abort(
        context.bot, chat_id, update.effective_user.id, context
    )
    q = update.callback_query
    if q:
        try:
            await q.answer("بازگشت به منوی اصلی")
        except Exception:
            pass
    return True


def _is_admin(uid: int) -> bool:
    return uid in set(ADMIN_IDS or [])


def _receipt_read_mode_hint() -> str:
    if receipt_vision_should_run():
        return "\n<i>خواندن فیش: AI + OCR</i>\n"
    if receipt_vision_available() and receipt_vision_uses_ollama():
        return "\n<i>خواندن فیش: OCR (Ollama روی CPU غیرفعال — سریع‌تر)</i>\n"
    if receipt_vision_available():
        return "\n<i>خواندن فیش: AI + OCR</i>\n"
    return "\n<i>خواندن فیش: OCR</i>\n"


# جداکنندهٔ هزارگان (ویرگول فارسی «،» با ویرگول لاتین فرق دارد)
_THOUSAND_SEPS = "،٬﹐,"


def _normalize_receipt_text(raw: str) -> str:
    """ارقام فارسی (۰-۹) و عربی-هندی (٠-٩) → لاتین؛ جداکنندهٔ هزارگان یکسان."""
    if not raw:
        return ""
    s = raw.replace("\u200c", " ").replace("\u200f", " ").replace("\u202a", " ")
    s = normalize_digits(s)
    for sep in _THOUSAND_SEPS:
        s = s.replace(sep, ",")
    s = s.replace("٫", ".")
    return _collapse_spaced_thousands(s)


def _collapse_spaced_thousands(text: str) -> str:
    """OCR: «1,199,000, 000» → «1,199,000,000» (بدون چسباندن خط بعدی مثل شماره پیگیری)."""
    s = text or ""
    for _ in range(4):
        s2 = re.sub(r"(?<=\d),\s+(?=\d)", ",", s)
        # فقط تکه‌های ۱–۳ رقمی جدا شده با فاصله (ادامه هزارگان)، نه اعداد بلند پیگیری
        s2 = re.sub(r"(?<=\d)\s+(?=\d{1,3}(?:\D|$))", "", s2)
        if s2 == s:
            break
        s = s2
    return s


def _normalize_receipt_amount(value: int) -> int:
    return normalize_transfer_amount(int(value or 0))


def _amount_from_mablagh_line(raw: str) -> int:
    """مبلغ دقیق از خط «مبلغ … ریال» — ارقام فارسی/ویرگول فارسی."""
    t = _normalize_receipt_text(raw or "")
    best = 0
    for line in t.splitlines():
        if not _MABLAGH_LINE.search(line):
            continue
        if re.search(r"حساب|شبا|پیگیری|IR", line, re.I):
            continue
        m = re.search(
            rf"([\d{''.join(_THOUSAND_SEPS)}\s]{{4,24}})\s*ریال",
            line,
            re.I,
        )
        if m:
            chunk = m.group(1)
            v = _normalize_receipt_amount(_parse_amount_rial(chunk))
            if v > best:
                best = v
        blob = re.sub(r"[^\d]", "", line)
        for width in (8, 9, 10):
            for i in range(max(0, len(blob) - width + 1)):
                try:
                    v = int(blob[i : i + width])
                except ValueError:
                    continue
                v = _normalize_receipt_amount(v)
                if v > best:
                    best = v
    return best


def _reconcile_receipt_amount(payload: dict, raw: str) -> dict:
    """اولویت با خط «مبلغ»؛ اصلاح ۱۰× بودن."""
    out = dict(payload)
    line_amt = _amount_from_mablagh_line(raw)
    try:
        cur = int(out.get("iran_amount") or 0)
    except (TypeError, ValueError):
        cur = 0
    if line_amt >= _MIN_RECEIPT_RIAL:
        if cur in (line_amt * 10, line_amt * 100) or not cur:
            out["iran_amount"] = line_amt
        elif cur > line_amt * 5 and line_amt >= 5_000_000:
            out["iran_amount"] = line_amt
    elif cur >= _MIN_RECEIPT_RIAL:
        fixed = _normalize_receipt_amount(cur)
        if fixed:
            out["iran_amount"] = fixed
    return out


def _maybe_fix_ocr_billion_typo(value: int, token: str = "") -> int:
    """۹ در ۱,۹۹۹ گاهی ۱ یا ۸ خوانده می‌شود (مثلاً 1,199,000,000)."""
    v = int(value)
    if v < 1_000_000_000 or v >= 2_000_000_000:
        return v
    if _comma_groups(token) < 2 and len(str(v)) < 10:
        return v
    s = str(v)
    if len(s) == 10 and s[0] == "1" and s[1:4] in ("199", "189", "198"):
        return 1_999_000_000
    return v


def _digits_only(s: str) -> str:
    return digits_only_ascii(s)


def _norm(s: str) -> str:
    return (s or "").strip()


def _extract_field(text: str, keys: list[str]) -> str:
    """
    Accept lines like:
      مبلغ: 120,000,000
      بانک: ملی
      نام: علی
    """
    t = text or ""
    for k in keys:
        m = re.search(rf"(?mi)^\s*{re.escape(k)}\s*[:：]\s*(.+?)\s*$", t)
        if m:
            return m.group(1).strip()
    return ""


def _guess_today_jdate() -> str:
    # Panel supports manual jdate; if omitted, backend likely sets it.
    # Keep empty to let server default when possible.
    return ""


def _parse_amount_rial(text: str) -> int:
    return parse_rial_amount_text(text)


def _parse_fee_tax(text: str) -> int:
    return _parse_amount_rial(text)


_MIN_RIAL_AMOUNT = 1_000
_MAX_RIAL_AMOUNT = 9_999_999_999_999
# حوالهٔ بانکی معمولاً حداقل یک میلیون ریال است؛ زیر این از OCR رد می‌شود.
_MIN_RECEIPT_RIAL = 1_000_000

# کد بانک در شبا (۳ رقم بعد از رقم کنترل IR)
_SHEBA_BANK_NAMES: dict[str, str] = {
    "010": "مرکزی",
    "011": "صنعت و معدن",
    "012": "ملت",
    "013": "رفاه",
    "014": "مسکن",
    "015": "سپه",
    "016": "کشاورزی",
    "017": "ملی",
    "018": "تجارت",
    "019": "صادرات",
    "020": "توسعه صادرات",
    "021": "پست بانک",
    "022": "توسعه تعاون",
    "051": "موسسه اعتباری توسعه",
    "052": "قوامین",
    "053": "کارآفرین",
    "054": "پارسیان",
    "055": "اقتصاد نوین",
    "056": "سامان",
    "057": "پاسارگاد",
    "058": "سرمایه",
    "059": "سینا",
    "060": "قرض‌الحسنه مهر",
    "061": "شهر",
    "062": "آینده",
    "063": "انصار",
    "064": "گردشگری",
    "065": "حکمت ایرانیان",
    "066": "دی",
    "069": "ایران‌زیرین",
    "070": "رسالت",
    "073": "موسسه اعتباری کوثر",
    "075": "موسسه اعتباری ملل",
    "078": "خاورمیانه",
    "080": "مشترک ایران-ونزوئلا",
}

# پیش‌شمارهٔ کارت (۶ رقم) → نام کوتاه بانک
_CARD_BIN_TO_BANK: dict[str, str] = {
    "603799": "ملی",
    "621986": "سامان",
    "610433": "ملت",
    "589463": "رفاه",
    "627353": "تجارت",
    "585983": "تجارت",
    "502229": "پاسارگاد",
    "636214": "آینده",
    "606373": "مهر",
    "622106": "پارسیان",
    "603769": "صادرات",
    "627412": "اقتصاد نوین",
    "639607": "سرمایه",
    "627488": "کارآفرین",
    "639346": "سینا",
    "504172": "رسالت",
    "505416": "گردشگری",
    "585947": "خاورمیانه",
    "628023": "مسکن",
    "639599": "قوامین",
}


def _bank_from_card_number(card: str) -> str:
    digits = _digits_only(card)
    if len(digits) < 6:
        return ""
    return _CARD_BIN_TO_BANK.get(digits[:6], "")


def _extract_labeled_card(raw: str, label_patterns: list[str]) -> str:
    t = _normalize_receipt_text(raw or "")
    for pat in label_patterns:
        m = re.search(
            rf"{pat}\s*[:：]?\s*([\d\sXx*]{{8,24}})",
            t,
            re.I,
        )
        if m:
            chunk = m.group(1)
            digits = _digits_only(chunk.replace("X", "").replace("x", "").replace("*", ""))
            if len(digits) >= 8:
                return digits
    return ""


def _guess_banks_from_cards(raw: str) -> tuple[str, str]:
    """بانک مبدأ/مقصد از خطوط «از کارت» / «به کارت» (لوگو یا متن)."""
    to_card = _extract_labeled_card(
        raw,
        [r"به\s*کارت", r"کارت\s*مقصد", r"مقصد\s*کارت"],
    )
    from_card = _extract_labeled_card(
        raw,
        [r"از\s*کارت", r"کارت\s*مبدا", r"مبدا\s*کارت", r"کارت\s*مبدأ"],
    )
    src = _bank_from_card_number(from_card) if from_card else ""
    dest = _bank_from_card_number(to_card) if to_card else ""
    return src, dest


def _normalize_bank_input(val: str) -> str:
    v = (val or "").strip()
    if not v or v == "-":
        return ""
    v = v.replace("بانک", "").strip()
    aliases = {
        "melli": "ملی",
        "meli": "ملی",
        "ملی": "ملی",
        "saman": "سامان",
        "سامان": "سامان",
        "mellat": "ملت",
        "ملت": "ملت",
        "blu": "بلو",
        "بلو": "بلو",
        "baam": "ملی",
        "bmi": "ملی",
    }
    low = v.lower()
    for key, name in aliases.items():
        if key in low or v == name:
            return name
    return v


def _is_plausible_rial_amount(value: int) -> bool:
    return _MIN_RIAL_AMOUNT <= int(value) <= _MAX_RIAL_AMOUNT


def _is_valid_receipt_transfer_amount(value: int, token: str = "") -> bool:
    """
    مبلغ معتبر حواله/کارت‌به‌کارت — از چند میلیون تا چند میلیارد ریال.
    """
    v = int(value)
    if not _is_plausible_rial_amount(v):
        return False
    if _looks_like_tracking_or_card(v):
        return False
    groups = _comma_groups(token)
    digits = len(str(v))
    if v % 1000 != 0:
        return False
    if groups >= 2:
        return True
    if digits >= 11:
        return False
    if digits >= 7 and v >= 5_000_000:
        return True
    return False


def _looks_like_tracking_or_card(value: int) -> bool:
    """پیگیری/کارت/شماره حساب — معمولاً ۱۲ رقم یا بیشتر و بدون جداکنندهٔ هزارگان."""
    ds = str(int(value))
    if len(ds) >= 14:
        return True
    if len(ds) >= 11 and ds.startswith("14"):
        return True
    if len(ds) >= 12 and int(value) % 1000 != 0:
        return True
    return False


_BAD_AMOUNT_CTX = re.compile(
    r"پیگیری|شماره\s*پی|شماره\s*کارت|شماره\s*حساب|کارت|حساب|شبا|شماره|مبدا|متعلق|"
    r"کد\s*ملی|انقضا|شروع\s*انجام|IR\d",
    re.IGNORECASE,
)
_MABLAGH_LINE = re.compile(r"مبلغ", re.IGNORECASE)
_FEE_AMOUNT_CTX = re.compile(r"کارمزد|مالیات|کمیسیون|کار\s*مزد", re.IGNORECASE)


def _amount_context_penalty(raw: str, start: int, end: int) -> int:
    t = _normalize_receipt_text(raw)
    window = t[max(0, start - 40) : min(len(t), end + 40)]
    pen = 0
    if _BAD_AMOUNT_CTX.search(window):
        pen += 80
    if _FEE_AMOUNT_CTX.search(window):
        pen += 90
    return pen


def _score_amount(
    value: int,
    *,
    near_keyword: bool,
    has_commas: bool,
    comma_groups: int,
    raw: str,
    start: int,
    end: int,
) -> int:
    score = 0
    if near_keyword:
        score += 120
    if has_commas:
        score += 25
    if comma_groups >= 2:
        score += 40
    if comma_groups >= 3:
        score += 60
    digits = len(str(int(value)))
    if 8 <= digits <= 11:
        score += 35
    elif digits <= 7:
        score += 10
    else:
        score -= 30
    if int(value) % 1_000_000 == 0:
        score += 20
    elif int(value) % 1000 != 0:
        score -= 80
    if comma_groups == 2:
        score += 90
    if int(value) < _MIN_RECEIPT_RIAL:
        score -= 70 if not near_keyword else 25
    if _looks_like_tracking_or_card(value):
        score -= 100
    score -= _amount_context_penalty(raw, start, end)
    return score


_AMT_COMMA = r"(?:,\s*\d{3})"


def _comma_groups(token: str) -> int:
    return len(re.findall(r",\s*\d{3}", token or ""))


def _amount_token_score(
    token: str, v: int, *, near_keyword: bool, raw: str, start: int, end: int
) -> int:
    return _score_amount(
        v,
        near_keyword=near_keyword,
        has_commas="," in token,
        comma_groups=_comma_groups(token),
        raw=raw,
        start=start,
        end=end,
    )


def _scan_amount_tokens(t: str, raw: str, scored: list[tuple[int, int]]) -> None:
    """همهٔ الگوهای عددی با جداکننده (بعد از نرمال فارسی→انگلیسی)."""
    pat = rf"(?<!\d)(\d{{1,3}}(?:{_AMT_COMMA}){{2,3}}|\d{{1,3}}(?:{_AMT_COMMA})+|\d{{9,11}})(?!\d)"
    for m in re.finditer(pat, t):
        token = m.group(1)
        v = _maybe_fix_ocr_billion_typo(_parse_amount_rial(token), token)
        if not _is_valid_receipt_transfer_amount(v, token):
            continue
        ctx = t[max(0, m.start() - 50) : m.end() + 50]
        near_kw = bool(re.search(r"مبلغ|حواله|ریال", ctx, re.I))
        sc = _amount_token_score(token, v, near_keyword=near_kw, raw=raw, start=m.start(), end=m.end())
        scored.append((v, sc))


def _extract_amount_from_receipt_lines(raw: str, scored: list[tuple[int, int]]) -> None:
    """خط «مبلغ» و ۱–۲ خط بعد (رسید baam و …)."""
    norm_lines = [_normalize_receipt_text(ln) for ln in (raw or "").splitlines()]
    pat = rf"(\d{{1,3}}(?:{_AMT_COMMA}){{2}}|\d{{1,3}}(?:{_AMT_COMMA})+)"
    for i, line in enumerate(norm_lines):
        if not _MABLAGH_LINE.search(line):
            continue
        if re.search(r"حساب|شبا|پیگیری|مبدا", line, re.I):
            continue
        block = "\n".join(norm_lines[i : i + 3])
        for m in re.finditer(pat, block):
            token = m.group(1)
            v = _maybe_fix_ocr_billion_typo(_parse_amount_rial(token), token)
            if not _is_valid_receipt_transfer_amount(v, token):
                continue
            sc = _amount_token_score(token, v, near_keyword=True, raw=raw, start=0, end=0)
            scored.append((v, sc + 120))
        # OCR: «مبلغع ۷۰ ریال» — ارقام پراکنده در همان خط
        if re.search(r"ریال", block, re.I):
            line_digits = "".join(c for c in line if c.isdigit())
            if len(line_digits) >= 8:
                v = _maybe_fix_ocr_billion_typo(int(line_digits[:11]), line_digits)
                if _is_valid_receipt_transfer_amount(v, line_digits):
                    scored.append(
                        (v, _amount_token_score(line_digits, v, near_keyword=True, raw=raw, start=0, end=0) + 40)
                    )


def _extract_comma_near_rial(t: str, raw: str, scored: list[tuple[int, int]]) -> None:
    """خطوط دارای «ریال» و عدد سه‌بخشی — حتی اگر OCR «مبلغ ۰ ریال» بدهد."""
    for line in t.splitlines():
        if not re.search(r"ریال", line, re.I):
            continue
        if OCR_ZERO_RIAL_RE.search(line):
            continue
        for m in re.finditer(rf"(\d{{1,3}}(?:{_AMT_COMMA}){{2}})\s*ریال", line, re.I):
            token = m.group(1)
            v = _parse_amount_rial(token)
            if not _is_valid_receipt_transfer_amount(v, token):
                continue
            sc = _amount_token_score(token, v, near_keyword=True, raw=raw, start=0, end=0)
            scored.append((v, sc + 160))


def _extract_comma_amounts_global(t: str, raw: str, scored: list[tuple[int, int]]) -> None:
    """هر عدد با دو ویرگول (۲۸۷,۶۲۵,۰۰۰) — اولویت بالا."""
    for m in re.finditer(rf"(?<!\d)(\d{{1,3}}(?:{_AMT_COMMA}){{2}})(?!\d)", t):
        token = m.group(1)
        v = _parse_amount_rial(token)
        if not _is_valid_receipt_transfer_amount(v, token):
            continue
        ctx = t[max(0, m.start() - 45) : m.end() + 45]
        near = bool(re.search(r"مبلغ|ریال|انتقال\s*پول", ctx, re.I))
        if _BAD_AMOUNT_CTX.search(ctx) and not near:
            continue
        sc = _amount_token_score(token, v, near_keyword=near, raw=raw, start=m.start(), end=m.end())
        scored.append((v, sc + 100))


def _extract_compact_rial_near_rial(t: str, raw: str, scored: list[tuple[int, int]]) -> None:
    """۹–۱۱ رقم فقط اگر در همان خط «مبلغ» باشد (نه شماره حساب/شبا)."""
    for i, line in enumerate(t.splitlines()):
        if not _MABLAGH_LINE.search(line):
            continue
        if re.search(r"حساب|شبا|پیگیری|IR", line, re.I):
            continue
        for m in re.finditer(r"(?<!\d)(\d{9,11})(?!\d)", line):
            token = m.group(1)
            v = int(token)
            if v < 5_000_000:
                continue
            if not re.search(r"ریال|مبلغ", line, re.I):
                continue
            sc = _amount_token_score(token, v, near_keyword=True, raw=raw, start=m.start(), end=m.end())
            scored.append((v, sc + 30))


def _dest_bank_from_sheba(raw: str) -> str:
    t = re.sub(r"\s+", "", _normalize_receipt_text(raw or "").upper())
    m = re.search(r"IR(\d{2})(\d{3})(\d+)", t)
    if m:
        bank_code = m.group(2)
        name = _SHEBA_BANK_NAMES.get(bank_code, "")
        if name and bank_code not in ("017",):  # مبدأ اغلب ملی
            return name
        tail = m.group(3)
        for code, bname in _SHEBA_BANK_NAMES.items():
            if code in ("017", "010"):
                continue
            if code in tail[:8]:
                return bname
    for m in re.finditer(r"IR\d{2}(\d{3})", t):
        name = _SHEBA_BANK_NAMES.get(m.group(1), "")
        if name and m.group(1) not in ("017",):
            return name
    return ""


def _resolve_receipt_amount(raw: str, labeled: int = 0) -> int:
    """فقط مبلغ معتبر حواله — نه بیشترین عدد تصادفی در OCR."""
    if labeled >= _MIN_RECEIPT_RIAL and _is_valid_receipt_transfer_amount(
        labeled, f"{labeled:,}"
    ):
        return labeled
    return _extract_best_amount(raw)


def _merge_payload(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if v is None:
            continue
        if k == "iran_amount":
            nv = _normalize_receipt_amount(int(v or 0))
            if nv >= _MIN_RECEIPT_RIAL:
                out[k] = nv
            continue
        if isinstance(v, str):
            if v.strip():
                out[k] = v.strip()
        elif v not in ("", 0):
            out[k] = v
    return out


def _extract_best_amount(raw: str) -> int:
    """
    انتخاب مبلغ حواله از OCR/متن رسید — ارقام فارسی، بزرگ‌ترین مبلغ نزدیک «مبلغ حواله».
    """
    t = _normalize_receipt_text(raw)
    scored: list[tuple[int, int]] = []

    amt = rf"\d{{1,3}}(?:{_AMT_COMMA}){{2,3}}|\d{{1,3}}(?:{_AMT_COMMA})+|\d{{9,11}}"
    keyword_patterns = [
        rf"مبلغ\w*[^\d]{{0,25}}\n\s*({amt})\s*ریال",
        rf"مبلغ\w*[^\d]{{0,40}}({amt})\s*ریال",
        rf"مبلغ\s*حواله\s*به\s*عدد[^\d]{{0,80}}({amt})",
        rf"مبلغ\s*حواله[^\d]{{0,80}}({amt})",
        rf"مبلغ\s*حواله[^\n]{{0,50}}\n\s*({amt})",
        rf"({amt})\s*ریال",
        rf"ریال[^\d]{{0,30}}({amt})",
    ]
    for pat in keyword_patterns:
        for m in re.finditer(pat, t, flags=re.IGNORECASE):
            token = m.group(1)
            v = _maybe_fix_ocr_billion_typo(_parse_amount_rial(token), token)
            if not _is_valid_receipt_transfer_amount(v, token):
                continue
            sc = _amount_token_score(token, v, near_keyword=True, raw=raw, start=m.start(), end=m.end())
            scored.append((v, sc))

    _extract_comma_amounts_global(t, raw, scored)
    _extract_comma_near_rial(t, raw, scored)
    _extract_amount_from_receipt_lines(raw, scored)
    _scan_amount_tokens(t, raw, scored)
    _extract_compact_rial_near_rial(t, raw, scored)

    if not scored:
        return 0

    # ترجیح: امتیاز بالا + در صورت تساوی، عدد با ویرگول معتبر (نه شماره حساب)
    scored.sort(key=lambda x: (x[1], -x[0]), reverse=True)
    best_val, best_sc = scored[0]
    if best_sc < 50:
        return 0
    if _comma_groups(f"{best_val:,}") < 2:
        for v, sc in scored[1:8]:
            if sc < best_sc - 35:
                break
            if _comma_groups(f"{v:,}") >= 2 and _is_valid_receipt_transfer_amount(v):
                best_val = v
                break
    if not _is_valid_receipt_transfer_amount(best_val):
        return 0
    return _normalize_receipt_amount(best_val)


def _fmt_rial_display(value) -> str:
    """نمایش مبلغ با ویرگول هزارگان (فقط نمایش؛ مقدار ذخیره‌شده عدد خام است)."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value or "").strip() or "—"


def _jdate_from_tracking_digits(ds: str) -> str:
    if len(ds) < 8:
        return ""
    jd = _normalize_jdate(f"{ds[0:4]}/{ds[4:6]}/{ds[6:8]}")
    if not jd:
        return ""
    try:
        y = int(jd.split("/")[0])
    except (ValueError, IndexError):
        return ""
    return jd if 1400 <= y <= 1410 else ""


def _extract_jdate_from_tracking(raw: str) -> str:
    """
    در رسید بانکی شماره پیگیری اغلب با YYYYMMDD شروع می‌شود (مثلاً 14050303…).
    وقتی OCR تاریخ را نمی‌خواند، از همین استفاده می‌کنیم.
    """
    t = _normalize_receipt_text(raw)
    for m in re.finditer(
        r"(?:شماره\s*)?پیگیری\s*[:：]?\s*(\d{14,22})",
        t,
        flags=re.IGNORECASE,
    ):
        jd = _jdate_from_tracking_digits(m.group(1))
        if jd:
            return jd
    for m in re.finditer(r"(?<!\d)(\d{14,22})(?!\d)", t):
        jd = _jdate_from_tracking_digits(m.group(1))
        if jd:
            return jd
    # اگر OCR مبلغ و پیگیری را به هم چسبانده: …14050303012243913947
    for m in re.finditer(r"(14\d{2})(\d{2})(\d{2})(?=\d{6,14})", t):
        jd = _normalize_jdate(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
        if jd:
            try:
                y = int(jd.split("/")[0])
            except (ValueError, IndexError):
                continue
            if 1400 <= y <= 1410:
                return jd
    return ""


_JALALI_MONTHS: dict[str, int] = {
    "فروردین": 1,
    "اردیبهشت": 2,
    "ارديبهشت": 2,
    "خرداد": 3,
    "تیر": 4,
    "تير": 4,
    "مرداد": 5,
    "شهریور": 6,
    "شهريور": 6,
    "مهر": 7,
    "آبان": 8,
    "ابان": 8,
    "آذر": 9,
    "اذر": 9,
    "دی": 10,
    "دي": 10,
    "بهمن": 11,
    "اسفند": 12,
}
_JALALI_MONTH_ALT = "|".join(
    sorted((re.escape(k) for k in _JALALI_MONTHS), key=len, reverse=True)
)


def _extract_jdate_from_persian_words(raw: str) -> str:
    """مثلاً «۶ خرداد ۱۴۰۳» یا «12:25 چهارشنبه 6 خرداد 1403»."""
    t = _normalize_receipt_text(raw)
    pat = rf"(\d{{1,2}})\s*({_JALALI_MONTH_ALT})\s*(\d{{4}})"
    for m in re.finditer(pat, t, flags=re.IGNORECASE):
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        mo = _JALALI_MONTHS.get(month_s) or _JALALI_MONTHS.get(
            month_s.replace("ي", "ی").replace("ك", "ک")
        )
        if not mo:
            continue
        try:
            day, year = int(day_s), int(year_s)
        except ValueError:
            continue
        if not (1400 <= year <= 1410 and 1 <= day <= 31):
            continue
        return f"{year:04d}/{mo:02d}/{day:02d}"
    return ""


def _guess_bank_from_receipt(raw: str) -> str:
    t = raw or ""
    if re.search(r"baam\.bmi|bmi\.ir|\bbaam\b", t, re.I):
        return "ملی"
    if re.search(r"بانک\s*ملی", t, re.I):
        return "ملی"
    src, _ = _guess_banks_from_cards(t)
    if src:
        return src
    if re.search(r"بلو|\bblu\b", t, re.I):
        return "بلو"
    if re.search(r"سامان", t, re.I):
        return "سامان"
    if re.search(r"صادرات", t, re.I):
        return "صادرات"
    if re.search(r"پاسارگاد", t, re.I):
        return "پاسارگاد"
    return ""


def _guess_dest_bank_from_receipt(raw: str) -> str:
    t = raw or ""
    _, dest_card = _guess_banks_from_cards(t)
    if dest_card:
        return dest_card
    m = re.search(r"بلو\s*به\s*(\S+)", t, re.I)
    if m:
        dest = m.group(1).strip()
        if dest and len(dest) < 30 and dest not in ("کارت", "card", "Card"):
            return dest
    m = re.search(
        r"بانک\s*مقصد\s*[:：]?\s*(\S+(?:\s+\S+)?)",
        t,
        re.I,
    )
    if m:
        dest = m.group(1).strip()
        if dest and len(dest) < 40:
            return dest.replace("ایران", "").strip() or dest
    m = re.search(
        r"به\s*(سامان|ملت|ملی|پاسارگاد|تجارت|پارسیان|صادرات)",
        t,
        re.I,
    )
    if m:
        return m.group(1)
    return _dest_bank_from_sheba(raw)


def _guess_transfer_type_from_receipt(raw: str) -> str:
    t = raw or ""
    if re.search(r"کارت\s*به\s*کارت|کارتبهکارت", t, re.I):
        return "کارت به کارت"
    m = re.search(r"بلو\s*به\s*(\S+)", t, re.I)
    if m:
        dest = m.group(1).strip()
        if dest and dest not in ("کارت", "card"):
            return f"بلو به {dest}"
    if re.search(r"پایا|بین\s*بانک", t, re.I):
        return "بین بانکی (پایا)"
    if re.search(r"\bپل\b|پل\s*پایا", t, re.I):
        return "پل"
    if re.search(r"ساتنا", t, re.I):
        return "ساتنا"
    return ""


def _extract_top_account_holder_name(raw: str) -> str:
    """نام درشت بالای رسید (صاحب حساب مقصد) — قبل از مبلغ، نه «انتقال دهنده»."""
    t = _normalize_receipt_text(raw or "")
    skip = re.compile(
        r"رسید|ریال|مبلغ|انتقال|موفق|زمان|سپرده|شماره|روش|سند|IR\d|بلو|سامان|"
        r"اشتراک|گالری|ذخیره|Transfer",
        re.I,
    )
    for line in t.splitlines()[:14]:
        line = line.strip()
        if not line or len(line) < 4 or len(line) > 70:
            continue
        if skip.search(line):
            continue
        if re.search(r"\d{5,}", line):
            continue
        if re.search(r"انتقال\s*دهنده", line, re.I):
            continue
        if re.match(r"^[\u0600-\u06FFa-zA-Z\s·]{3,70}$", line) and len(line.split()) >= 2:
            return line
    return ""


def _extract_recipient_name_from_receipt(raw: str) -> str:
    """نام مقصد / واریزکننده روی رسیدهای بانکی."""
    top = _extract_top_account_holder_name(raw)
    if top:
        return top
    patterns = [
        r"نام\s*صاحب\s*حساب\s*مقصد\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"صاحب\s*حساب\s*مقصد\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"نام\s*واریزکننده\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"به\s+([^\n\d]{3,60}?)\s*(?:\n|$)",
    ]
    for pat in patterns:
        m = re.search(pat, raw or "", flags=re.IGNORECASE)
        if not m:
            continue
        name = m.group(1).strip()
        if not name or len(name) > 80:
            continue
        if re.search(r"ریال|مبلغ|حساب|شبا|پیگیری", name, re.I):
            continue
        return name
    return ""


def _normalize_jdate(raw: str) -> str:
    s = _normalize_receipt_text(raw).strip()
    s = re.sub(r"\s+", "", s.replace("-", "/"))
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if y < 1300 or y > 1500 or mo < 1 or mo > 12 or d < 1 or d > 31:
        return ""
    return f"{y:04d}/{mo:02d}/{d:02d}"


def _extract_jdate(raw: str) -> str:
    """تاریخ موثر / زمان تراکنش از متن رسید (شمسی، ارقام فارسی)."""
    t = _normalize_receipt_text(raw)
    date_pat = r"(\d{4}\s*[/\-]\s*\d{1,2}\s*[/\-]\s*\d{1,2})"
    tarikh_kw = r"ت[اآ]ر[يیخ]"

    m = re.search(
        rf"{date_pat}\s*[-–]\s*\d{{1,2}}:\d{{2}}",
        t,
    )
    if m:
        jd = _normalize_jdate(m.group(1))
        if jd:
            return jd

    patterns = [
        rf"{tarikh_kw}\s*م?و?ث?ر[^\d]{{0,60}}{date_pat}",
        rf"زمان\s*تراکنش[^\d]{{0,50}}{date_pat}",
        rf"زمان[^\d]{{0,55}}{date_pat}",
        rf"{tarikh_kw}[^\n]{{0,40}}\n\s*{date_pat}",
        rf"{tarikh_kw}[^\d]{{0,30}}{date_pat}",
        rf"موثر[^\d]{{0,25}}{date_pat}",
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            jd = _normalize_jdate(m.group(1))
            if jd:
                return jd

    for line in t.splitlines():
        if re.search(r"پیگیری|شماره\s*پی", line, re.I):
            continue
        if re.search(
            rf"{tarikh_kw}|زمان\s*تراکنش|زمان\s*انتقال|موثر|تاریخ",
            line,
            re.I,
        ):
            m = re.search(date_pat, line)
            if m:
                jd = _normalize_jdate(m.group(1))
                if jd:
                    return jd

    for m in re.finditer(date_pat, t):
        ctx = t[max(0, m.start() - 35) : m.start()]
        if re.search(r"پیگیری|شماره\s*پی", ctx, re.I):
            continue
        jd = _normalize_jdate(m.group(1))
        if not jd:
            continue
        try:
            y = int(jd.split("/")[0])
        except (ValueError, IndexError):
            continue
        if 1400 <= y <= 1410:
            return jd
    jd = _extract_jdate_from_persian_words(raw)
    if jd:
        return jd
    jd = _extract_jdate_from_tracking(t)
    if jd:
        return jd
    return ""


def _extract_depositor_from_receipt(raw: str) -> str:
    t = raw or ""
    patterns = [
        r"نام\s*و\s*نام\s*خانوادگی\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"انتقال\s*دهنده\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"متعلق\s*به\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"نام\s*صاحب\s*حساب\s*مقصد\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"صاحب\s*حساب\s*مقصد\s*[:：]?\s*(.+?)\s*(?:\n|$)",
        r"نام\s*واریزکننده\s*[:：]?\s*(.+?)\s*(?:\n|$)",
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            if name and len(name) < 80:
                return name
    return ""


def _extract_description_from_receipt(raw: str) -> str:
    parts: list[str] = []
    for key in ("شرح", "بابت", "توضیحات"):
        v = _extract_field(raw, [key])
        if v and v not in parts:
            parts.append(v)
    return " · ".join(parts)


def _find_amount_anywhere(raw: str) -> int:
    return _extract_best_amount(raw)


def _find_amount_near_keywords(raw: str) -> int:
    return _extract_best_amount(raw)


def _coerce_vision_str(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("null", "none", "-") else s


def _coerce_vision_amount(val) -> int:
    try:
        if isinstance(val, str):
            s = _normalize_receipt_text(val).replace(",", "").replace(" ", "")
            v = int(s) if s.isdigit() else 0
        else:
            v = int(val)
        return _normalize_receipt_amount(v)
    except (TypeError, ValueError):
        return 0


def _payload_from_vision(vision: dict, mode: str) -> dict:
    """ادغام JSON مدل بینایی با پیش‌فرض‌های پنل."""
    if mode == "in":
        payload, _ = _parse_payload_in("")
    else:
        payload, _ = _parse_payload_out("")

    amt = _coerce_vision_amount(vision.get("iran_amount"))
    if amt:
        payload["iran_amount"] = amt

    jd = _normalize_jdate(_coerce_vision_str(vision.get("jdate")))
    if jd:
        payload["jdate"] = jd

    bank = _coerce_vision_str(vision.get("bank_name"))
    if bank:
        payload["bank_name"] = bank

    if mode == "out":
        dest = _coerce_vision_str(vision.get("dest_bank"))
        ttype_raw = _coerce_vision_str(vision.get("transfer_type"))
        if not dest and ttype_raw:
            m = re.search(r"بلو\s*به\s*(\S+)", ttype_raw, re.I)
            if m and m.group(1).strip() not in ("کارت", "card"):
                dest = m.group(1).strip()
        if dest and dest not in ("کارت", "card"):
            payload["dest_bank"] = dest
        if not bank and re.search(r"بلو", ttype_raw, re.I) and not re.search(
            r"به\s*کارت", ttype_raw, re.I
        ):
            payload["bank_name"] = "بلو"

    recipient = _coerce_vision_str(vision.get("recipient_name"))
    sender = _coerce_vision_str(vision.get("sender_name"))
    name = _coerce_vision_str(vision.get("depositor_name"))
    if mode == "out":
        pick = recipient or name
        if pick and sender and pick == sender and recipient:
            pick = recipient
        if pick:
            payload["depositor_name"] = pick
    elif name:
        payload["depositor_name"] = name

    ttype = _coerce_vision_str(vision.get("transfer_type"))
    if ttype:
        payload["transfer_type"] = ttype

    desc = _coerce_vision_str(vision.get("description"))
    if desc:
        payload["description"] = desc

    return payload


def _enrich_payload_from_ocr_text(payload: dict, raw: str, mode: str) -> dict:
    """مبلغ/تاریخ/نام/بانک از OCR — وقتی layout فیش فرق می‌کند یا vision ناقص است."""
    out = dict(payload)
    cur = int(out.get("iran_amount") or 0)
    if cur >= _MIN_RECEIPT_RIAL:
        fixed = _normalize_receipt_amount(cur)
        if fixed:
            out["iran_amount"] = fixed
    if not (raw or "").strip():
        return out
    if int(out.get("iran_amount") or 0) < _MIN_RECEIPT_RIAL:
        amt = _normalize_receipt_amount(_extract_best_amount(raw))
        if amt >= _MIN_RECEIPT_RIAL:
            out["iran_amount"] = amt
    jd = _extract_jdate_from_persian_words(raw) or _extract_jdate(raw)
    if jd:
        out["jdate"] = jd
    ocr_bank = _guess_bank_from_receipt(raw)
    ocr_dest = _guess_dest_bank_from_receipt(raw) if mode == "out" else ""
    cur_bank = (out.get("bank_name") or "").strip()
    if ocr_bank and (not cur_bank or (cur_bank == "بلو" and ocr_bank != "بلو")):
        out["bank_name"] = ocr_bank
    if mode == "out":
        cur_dest = (out.get("dest_bank") or "").strip()
        if ocr_dest and (not cur_dest or cur_dest in ("کارت", "card")):
            out["dest_bank"] = ocr_dest
        holder = _extract_top_account_holder_name(raw) or _extract_recipient_name_from_receipt(
            raw
        )
        if holder:
            out["depositor_name"] = holder
        if re.search(r"کارت\s*به\s*کارت", raw, re.I):
            out["transfer_type"] = "کارت به کارت"
        elif not (out.get("transfer_type") or "").strip():
            guessed = _guess_transfer_type_from_receipt(raw)
            if guessed:
                out["transfer_type"] = guessed
    return _reconcile_receipt_amount(out, raw)


def _salvage_amount_on_payload(payload: dict, path: str) -> dict:
    """آخرین تلاش: نوار مبلغ روی تصویر (مستقل از جای متن در فیش)."""
    out = dict(payload)
    if int(out.get("iran_amount") or 0) >= _MIN_RECEIPT_RIAL:
        return out
    try:
        img_amt = ocr_best_amount_from_image(path, budget_sec=50)
    except Exception:
        logger.exception("iran_panel: salvage amount failed")
        img_amt = 0
    if img_amt >= _MIN_RECEIPT_RIAL:
        out["iran_amount"] = _normalize_receipt_amount(img_amt)
        logger.info("iran_panel: salvage amount from image=%s", img_amt)
    return out


def _vision_amount_ok(vision: dict | None) -> bool:
    return _coerce_vision_amount((vision or {}).get("iran_amount")) >= _MIN_RECEIPT_RIAL


async def _read_receipt_image(path: str, mode: str) -> tuple[dict | None, str, str]:
    """
    خواندن رسید: OpenAI بینایی اول (سریع)، Ollama موازی با OCR، بدون API فقط OCR.
    برمی‌گرداند: (payload مستقیم یا None, متن خام OCR, منبع: vision|ocr|"" )
    """
    from config.settings import RECEIPT_VISION_TIMEOUT_SEC
    from utils.receipt_vision import (
        extract_receipt_with_vision,
        receipt_vision_available,
        receipt_vision_should_run,
    )

    vision_partial: dict | None = None
    use_ai = receipt_vision_available()
    run_vision = receipt_vision_should_run()
    openai_vision = run_vision and not receipt_vision_uses_ollama()
    if use_ai and not run_vision:
        logger.info(
            "iran_panel: skip Ollama vision (set RECEIPT_VISION_USE_OLLAMA=1 to enable)"
        )
    if openai_vision:
        vision_timeout = min(float(RECEIPT_VISION_TIMEOUT_SEC or 90), 90.0)
    else:
        vision_timeout = float(RECEIPT_VISION_TIMEOUT_SEC) if receipt_vision_uses_ollama() else 120.0
    ocr_timeout = 35.0 if openai_vision else (90.0 if not run_vision else 55.0)
    baam_budget = 25 if openai_vision else 55

    async def _run_vision() -> dict | None:
        if not run_vision:
            return None
        try:
            return await asyncio.wait_for(
                extract_receipt_with_vision(path, mode=mode),
                timeout=vision_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "iran_panel: vision timed out after %.0fs", vision_timeout
            )
            return None
        except Exception:
            logger.exception("iran_panel: vision failed")
            return None

    async def _run_ocr(
        *, quick: bool | None = None, timeout: float | None = None
    ) -> tuple[bool, str]:
        ocr_quick = quick if quick is not None else run_vision
        ocr_to = timeout if timeout is not None else ocr_timeout
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(ocr_image_to_text, path, quick=ocr_quick),
                timeout=ocr_to,
            )
        except asyncio.TimeoutError:
            logger.warning("iran_panel: ocr timed out (quick=%s)", ocr_quick)
            return False, ""
        except Exception:
            logger.exception("iran_panel: ocr failed")
            return False, ""

    async def _run_baam_amount(budget: float | None = None) -> int:
        b = budget if budget is not None else baam_budget
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    ocr_best_amount_from_image, path, budget_sec=b
                ),
                timeout=float(b + 10),
            )
        except asyncio.TimeoutError:
            logger.warning("iran_panel: baam amount pass timed out")
            return 0
        except Exception:
            logger.exception("iran_panel: baam amount pass failed")
            return 0

    vision: dict | None = None
    baam_amt = 0
    ok_ocr, txt = False, ""

    if openai_vision:
        vision = await _run_vision()
        if vision and _vision_amount_ok(vision):
            ok_ocr, txt = await _run_ocr(quick=True, timeout=35.0)
            payload = _payload_from_vision(vision, mode)
            if (txt or "").strip():
                payload = _enrich_payload_from_ocr_text(payload, txt, mode)
            else:
                payload = _reconcile_receipt_amount(payload, "")
            logger.info("iran_panel: read source=vision (fast path)")
            return payload, (txt or "").strip(), "vision"
        vision_partial = vision
        logger.info(
            "iran_panel: vision partial or empty — OCR+baam parallel (layout varies)"
        )
        baam_amt, (ok_ocr, txt) = await asyncio.gather(
            _run_baam_amount(50),
            _run_ocr(quick=False, timeout=68.0),
        )
    elif run_vision:
        vision, (ok_ocr, txt), baam_amt = await asyncio.gather(
            _run_vision(), _run_ocr(), _run_baam_amount()
        )
    else:
        baam_amt = await _run_baam_amount()
        logger.info("iran_panel: baam pass finished amount=%s", baam_amt or 0)
        ok_ocr, txt = await _run_ocr()

    if vision and not vision_partial:
        if _vision_amount_ok(vision):
            return _reconcile_receipt_amount(_payload_from_vision(vision, mode), ""), "", "vision"
        vision_partial = vision

    raw = (txt or "").strip()
    if baam_amt >= _MIN_RECEIPT_RIAL and not text_has_parseable_amount(raw):
        norm_baam = _normalize_receipt_amount(baam_amt) or baam_amt
        raw = f"مبلغ\n{norm_baam:,} ریال\n{raw}".strip()

    if not ok_ocr and not raw:
        if vision_partial:
            vp = _payload_from_vision(vision_partial, mode)
            vp = _salvage_amount_on_payload(vp, path)
            return vp, "", "vision_partial"
        if baam_amt >= _MIN_RECEIPT_RIAL:
            if mode == "in":
                payload, _ = _parse_payload_in(raw)
            else:
                payload, _ = _parse_payload_out(raw)
            payload["iran_amount"] = _normalize_receipt_amount(baam_amt) or baam_amt
            logger.info("iran_panel: salvage amount from baam band=%s", baam_amt)
            return payload, raw, "ocr_baam"
        if mode == "in":
            empty_pl, _ = _parse_payload_in("")
        else:
            empty_pl, _ = _parse_payload_out("")
        salvaged = _salvage_amount_on_payload(empty_pl, path)
        if int(salvaged.get("iran_amount") or 0) >= _MIN_RECEIPT_RIAL:
            logger.info("iran_panel: last-resort image amount=%s", salvaged["iran_amount"])
            return salvaged, "", "ocr_salvage"
        return None, "", "timeout" if use_ai else "ocr"

    if mode == "in":
        payload, _ = _parse_payload_in(raw)
    else:
        payload, _ = _parse_payload_out(raw)

    if int(payload.get("iran_amount") or 0) < _MIN_RECEIPT_RIAL:
        if baam_amt >= _MIN_RECEIPT_RIAL:
            payload["iran_amount"] = _normalize_receipt_amount(baam_amt) or baam_amt
            logger.info("iran_panel: amount from baam pass=%s", baam_amt)
        else:
            try:
                img_amt = await asyncio.wait_for(
                    asyncio.to_thread(ocr_best_amount_from_image, path, budget_sec=25),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                img_amt = 0
            if img_amt >= _MIN_RECEIPT_RIAL and _is_valid_receipt_transfer_amount(
                img_amt, f"{img_amt:,}"
            ):
                payload["iran_amount"] = _normalize_receipt_amount(img_amt) or img_amt
                logger.info("iran_panel: amount from image band=%s", img_amt)

    if vision_partial:
        payload = _merge_payload(payload, _payload_from_vision(vision_partial, mode))

    payload = _enrich_payload_from_ocr_text(payload, raw, mode)
    payload = _salvage_amount_on_payload(payload, path)

    return payload, raw, "ocr"


def _parse_payload_in(raw: str) -> tuple[dict, str]:
    bank = _extract_field(raw, ["بانک", "bank"]) or _guess_bank_from_receipt(raw) or "ملی"
    labeled = _parse_amount_rial(
        _extract_field(raw, ["مبلغ", "amount", "مبلغ (ریال)", "مبلغ حواله"])
    )
    amount = _normalize_receipt_amount(
        _resolve_receipt_amount(raw, labeled)
    )
    name = (
        _extract_field(raw, ["نام", "واریزکننده", "نام واریزکننده", "depositor", "name"])
        or _extract_depositor_from_receipt(raw)
        or _extract_recipient_name_from_receipt(raw)
    )
    ttype = (
        _extract_field(raw, ["نوع", "نوع حواله", "transfer", "transfer_type"])
        or _guess_transfer_type_from_receipt(raw)
        or "کارت به کارت"
    )
    jdate = (
        _normalize_jdate(_extract_field(raw, ["تاریخ موثر", "تاریخ", "تاریخ (شمسی)", "jdate"]))
        or _extract_jdate(raw)
        or _guess_today_jdate()
    )
    desc = _extract_description_from_receipt(raw) or _extract_field(
        raw, ["توضیح", "توضیحات", "description", "desc"]
    )
    payload = {
        "type": "ایران",
        "iran_type": "ورودی",
        "bank_name": bank,
        "iran_amount": amount,
        "depositor_name": name or "",
        "transfer_type": ttype or "کارت به کارت",
        "deposit_fee": 0,
        "tax": 0,
        "description": desc or "",
        "jdate": jdate or "",
    }
    return payload, ""


def _parse_payload_out(raw: str) -> tuple[dict, str]:
    bank = (
        _extract_field(raw, ["بانک", "بانک منبع", "bank"])
        or _guess_bank_from_receipt(raw)
        or "ملی"
    )
    dest = (
        _extract_field(raw, ["مقصد", "بانک مقصد", "dest", "destBank"])
        or _guess_dest_bank_from_receipt(raw)
    )
    labeled = _parse_amount_rial(
        _extract_field(raw, ["مبلغ", "amount", "مبلغ (ریال)", "مبلغ حواله"])
    )
    amount = _normalize_receipt_amount(
        _resolve_receipt_amount(raw, labeled)
    )
    name = (
        _extract_field(raw, ["نام", "برداشت‌کننده", "نام برداشت‌کننده", "name"])
        or _extract_top_account_holder_name(raw)
        or _extract_recipient_name_from_receipt(raw)
        or _extract_depositor_from_receipt(raw)
    )
    ttype = (
        _extract_field(raw, ["نوع", "نوع حواله", "transfer", "transfer_type"])
        or _guess_transfer_type_from_receipt(raw)
        or "کارت به کارت"
    )
    jdate = (
        _extract_jdate_from_persian_words(raw)
        or _normalize_jdate(
            _extract_field(raw, ["تاریخ موثر", "تاریخ", "تاریخ (شمسی)", "jdate"])
        )
        or _extract_jdate(raw)
        or _guess_today_jdate()
    )
    desc = _extract_description_from_receipt(raw) or _extract_field(
        raw, ["توضیح", "توضیحات", "description", "desc"]
    )
    payload = {
        "type": "ایران",
        "iran_type": "خروجی",
        "bank_name": bank,
        "dest_bank": dest,
        "iran_amount": amount,
        "depositor_name": name or "",  # panel uses depositor_name for both pages
        "transfer_type": ttype or "کارت به کارت",
        # do NOT send fee/tax: panel auto-calculates in خروجی
        "description": desc or "",
        "jdate": jdate or "",
    }
    return payload, ""


def _draft_missing_fields(payload: dict, mode: str) -> list[str]:
    """فقط فیلدهایی که بدون آن‌ها ثبت در پنل معنا ندارد — بقیه را ادمین ویرایش می‌کند."""
    missing: list[str] = []
    if int(payload.get("iran_amount") or 0) < _MIN_RECEIPT_RIAL:
        missing.append("iran_amount")
    if not (payload.get("bank_name") or "").strip():
        missing.append("bank_name")
    if not (payload.get("jdate") or "").strip():
        missing.append("jdate")
    return missing


def _draft_optional_empty(payload: dict, mode: str) -> list[str]:
    """خروجی: فیلدهایی که روی فیش بود ولی خوانده نشد — اختیاری در پنل."""
    if mode != "out":
        return []
    optional: list[str] = []
    if not (payload.get("depositor_name") or "").strip():
        optional.append("depositor_name")
    if not (payload.get("dest_bank") or "").strip():
        optional.append("dest_bank")
    return optional


def _panel_payload_for_submit(payload: dict, mode: str) -> dict:
    """بدنهٔ POST پنل — خروجی بدون کارمزد/مالیات (سایت خودش حساب می‌کند)."""
    if mode == "out":
        return {
            "type": "ایران",
            "iran_type": "خروجی",
            "bank_name": (payload.get("bank_name") or "").strip(),
            "dest_bank": (payload.get("dest_bank") or "").strip(),
            "iran_amount": int(payload.get("iran_amount") or 0),
            "depositor_name": (payload.get("depositor_name") or "").strip(),
            "transfer_type": (payload.get("transfer_type") or "کارت به کارت").strip(),
            "description": (payload.get("description") or "").strip(),
            "jdate": (payload.get("jdate") or "").strip(),
        }
    return {
        "type": "ایران",
        "iran_type": "ورودی",
        "bank_name": (payload.get("bank_name") or "").strip(),
        "iran_amount": int(payload.get("iran_amount") or 0),
        "depositor_name": (payload.get("depositor_name") or "").strip(),
        "transfer_type": (payload.get("transfer_type") or "کارت به کارت").strip(),
        "deposit_fee": int(payload.get("deposit_fee") or 0),
        "tax": int(payload.get("tax") or 0),
        "description": (payload.get("description") or "").strip(),
        "jdate": (payload.get("jdate") or "").strip(),
    }


def _field_label(field: str, mode: str = "in") -> str:
    labels = {
        "iran_amount": "مبلغ (ریال)",
        "bank_name": "بانک مبدأ" if mode == "out" else "بانک",
        "depositor_name": "نام برداشت‌کننده" if mode == "out" else "نام واریزکننده",
        "dest_bank": "بانک مقصد",
        "transfer_type": "نوع حواله",
        "jdate": "تاریخ (شمسی)",
        "description": "توضیحات",
    }
    return labels.get(field, field)


def _track_tx_message(context: ContextTypes.DEFAULT_TYPE, message_id: int | None) -> None:
    if not message_id:
        return
    ids: list[int] = context.user_data.setdefault(_TX_MSG_IDS_KEY, [])
    mid = int(message_id)
    if mid not in ids:
        ids.append(mid)


async def _delete_tx_prompt(context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int) -> None:
    pm = context.user_data.pop(_PROMPT_MID_KEY, None)
    if pm:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(pm))
        except Exception:
            pass


async def _cleanup_tx_flow(
    bot, chat_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _delete_tx_prompt(context, bot, chat_id)
    for key in (
        _DRAFT_KEY,
        _DRAFT_MODE_KEY,
        _AWAIT_FIELD_KEY,
        _KEY,
        _DRAFT_MID_KEY,
        _PROMPT_MID_KEY,
    ):
        context.user_data.pop(key, None)
    ids = context.user_data.pop(_TX_MSG_IDS_KEY, None) or []
    for mid in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(mid))
        except Exception:
            pass


async def _show_draft(
    bot,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    mode: str,
    payload: dict,
    *,
    edit_menu: bool = False,
) -> None:
    missing = _draft_missing_fields(payload, mode)
    optional_empty = _draft_optional_empty(payload, mode)
    body = _render_draft_html(mode, payload)
    if edit_menu:
        body += f"\n{_RTL}✏️ <b>کدام فیلد را ویرایش می‌کنید؟</b>"
        markup = _edit_field_keyboard(mode)
    else:
        if missing:
            body += (
                f"\n{_RTL}⚠️ برای ثبت، این موارد را اصلاح کنید: "
                f"<b>{'، '.join(_field_label(x, mode) for x in missing)}</b>"
            )
        elif optional_empty:
            body += (
                f"\n{_RTL}ℹ️ در صورت نیاز ویرایش کنید: "
                f"<b>{'، '.join(_field_label(x, mode) for x in optional_empty)}</b>"
            )
        if mode == "out" and not missing:
            body += f"\n{_RTL}<i>کارمزد و مالیات در سایت خودکار محاسبه می‌شود.</i>"
        markup = _draft_keyboard(can_submit=not missing)
    dm = context.user_data.get(_DRAFT_MID_KEY)
    if dm and not edit_menu:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(dm),
                text=body,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass
    if dm and edit_menu:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(dm),
                text=body,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass
    sent = await bot.send_message(
        chat_id,
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    context.user_data[_DRAFT_MID_KEY] = sent.message_id
    _track_tx_message(context, sent.message_id)


def _edit_field_keyboard(mode: str) -> InlineKeyboardMarkup:
    fields = _fields_for_mode(mode)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for f in fields:
        row.append(
            InlineKeyboardButton(
                f"✏️ {_field_label(f, mode)}",
                callback_data=f"tx|ef|{f}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [InlineKeyboardButton("◀️ بازگشت به پیش‌نویس", callback_data="tx|back")]
    )
    rows.append(
        [InlineKeyboardButton("❌ انصراف", callback_data="tx|cancel")]
    )
    return InlineKeyboardMarkup(rows)


def _render_draft_html(mode: str, payload: dict) -> str:
    title = "ورودی" if mode == "in" else "خروجی"
    def _s(k: str) -> str:
        v = payload.get(k)
        return (str(v).strip() if v is not None else "").strip()

    amt_raw = payload.get("iran_amount")
    try:
        amt_disp = _fmt_rial_display(int(amt_raw)) if amt_raw not in (None, "", 0) else "—"
    except (TypeError, ValueError):
        amt_disp = _s("iran_amount") or "—"

    if mode == "out":
        return (
            f"{_RTL}🧾 <b>پیش‌نویس ثبت {title}</b>\n\n"
            f"{_RTL}🏦 <b>بانک (منبع):</b> <code>{_s('bank_name') or '—'}</code>\n"
            f"{_RTL}🏁 <b>بانک مقصد:</b> <code>{_s('dest_bank') or '—'}</code>\n"
            f"{_RTL}💰 <b>مبلغ (ریال):</b> <code>{amt_disp}</code>\n"
            f"{_RTL}👤 <b>نام برداشت‌کننده (صاحب حساب):</b> "
            f"<code>{_s('depositor_name') or '—'}</code>\n"
            f"{_RTL}🔁 <b>نوع حواله:</b> <code>{_s('transfer_type') or '—'}</code>\n"
            f"{_RTL}🗓 <b>تاریخ (شمسی):</b> <code>{_s('jdate') or '—'}</code>\n"
            f"{_RTL}📝 <b>توضیحات:</b> <code>{_s('description') or '—'}</code>\n"
        )
    return (
        f"{_RTL}🧾 <b>پیش‌نویس ثبت {title}</b>\n\n"
        f"{_RTL}🏦 <b>بانک:</b> <code>{_s('bank_name') or '—'}</code>\n"
        f"{_RTL}💰 <b>مبلغ (ریال):</b> <code>{amt_disp}</code>\n"
        f"{_RTL}👤 <b>نام:</b> <code>{_s('depositor_name') or '—'}</code>\n"
        f"{_RTL}🔁 <b>نوع حواله:</b> <code>{_s('transfer_type') or '—'}</code>\n"
        f"{_RTL}🗓 <b>تاریخ:</b> <code>{_s('jdate') or '—'}</code>\n"
        f"{_RTL}📝 <b>توضیحات:</b> <code>{_s('description') or '—'}</code>\n"
    )


def _draft_keyboard(*, can_submit: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_submit:
        rows.append([InlineKeyboardButton("✅ ثبت", callback_data="tx|submit")])
    rows.append([InlineKeyboardButton("✏️ ویرایش فیلدها", callback_data="tx|fill")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="tx|cancel")])
    return InlineKeyboardMarkup(rows)


async def _download_receipt_to_temp(bot, message: "Update.message") -> str | None:
    m = message
    if not m:
        return None
    file_id = None
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.document:
        mt = (m.document.mime_type or "").lower()
        if mt.startswith("image/"):
            file_id = m.document.file_id
    if not file_id:
        return None
    f = await bot.get_file(file_id)
    tmp_dir = tempfile.gettempdir()
    path = os.path.join(tmp_dir, f"receipt_{file_id}.jpg")
    try:
        await f.download_to_drive(custom_path=path)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    except Exception as e:
        logger.warning("iran_panel download custom_path failed: %s", e)
    try:
        path2 = await f.download_to_drive()
        if path2 and os.path.isfile(path2) and os.path.getsize(path2) > 0:
            return str(path2)
    except Exception as e:
        logger.warning("iran_panel download failed: %s", e)
    return None


async def txin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _is_admin(update.effective_user.id):
        return
    chat_id = update.message.chat_id
    await _cleanup_tx_flow(context.bot, chat_id, context)
    context.user_data[_KEY] = "in"
    ai_hint = _receipt_read_mode_hint()
    sent = await update.message.reply_text(
        "📥 <b>ثبت ورودی در سایت ایران</b>\n\n"
        "عکس رسید را بفرستید — ربات از روی فیش <b>مبلغ، تاریخ، نام و …</b> را پر می‌کند.\n"
        "بعد با «<b>ویرایش فیلدها</b>» هر مورد را جداگانه اصلاح کنید.\n"
        f"{ai_hint}"
        "<i>کارمزد و مالیات ورودی: صفر (طبق سایت).</i>\n"
        "<i>برای لغو: دکمهٔ ❌ انصراف</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_tx_flow_keyboard(),
        disable_web_page_preview=True,
    )
    _track_tx_message(context, update.message.message_id)
    _track_tx_message(context, sent.message_id)


async def txout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _is_admin(update.effective_user.id):
        return
    chat_id = update.message.chat_id
    await _cleanup_tx_flow(context.bot, chat_id, context)
    context.user_data[_KEY] = "out"
    ai_hint = _receipt_read_mode_hint()
    sent = await update.message.reply_text(
        "📤 <b>ثبت خروجی در سایت ایران</b>\n\n"
        "عکس رسید را بفرستید — این فیلدها از فیش پر می‌شوند:\n"
        "• بانک (منبع)\n"
        "• بانک مقصد\n"
        "• مبلغ (ریال)\n"
        "• نام برداشت‌کننده (صاحب حساب — نام بالای رسید)\n"
        "• نوع حواله\n"
        "• تاریخ (شمسی)\n"
        "• توضیحات (اگر بود)\n\n"
        f"{ai_hint}"
        "<i>کارمزد و مالیات را وارد نکنید — سایت خودکار محاسبه می‌کند.</i>\n"
        "<i>هر چیز ناقص بود «ویرایش فیلدها».</i>\n"
        "<i>برای لغو: دکمهٔ ❌ انصراف</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_tx_flow_keyboard(),
        disable_web_page_preview=True,
    )
    _track_tx_message(context, update.message.message_id)
    _track_tx_message(context, sent.message_id)


async def iran_panel_sync_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Intercepts admin messages after /txin or /txout.
    """
    if not update.effective_user:
        return
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    m = update.message
    if not m:
        return

    has_media = bool(m.photo or m.document)
    mode = context.user_data.get(_KEY) or (
        context.user_data.get(_DRAFT_MODE_KEY) if has_media else None
    )

    from handlers.admin import _admin_should_skip_wizard_recovery

    iran_tx_active = is_iran_tx_flow_active(context) or mode in ("in", "out")
    if _admin_should_skip_wizard_recovery(context) and not (has_media and iran_tx_active):
        return

    if (context.user_data.get(_AWAIT_FIELD_KEY) or "").strip():
        if has_media:
            await m.reply_text(
                f"{_RTL}ℹ️ در حال ویرایش یک فیلد هستید.\n"
                f"{_RTL}متن بفرستید یا «◀️ بازگشت به پیش‌نویس» را بزنید."
            )
            raise ApplicationHandlerStop
        return

    if m.text and _is_tx_cancel_message(m.text):
        if await abort_iran_tx_flow_if_active(update, context):
            raise ApplicationHandlerStop

    if mode in ("in", "out") or has_media:
        logger.info(
            "iran_panel: uid=%s mode=%s photo=%s doc=%s",
            uid,
            mode,
            bool(m.photo),
            bool(m.document),
        )

    if mode not in ("in", "out"):
        if has_media:
            await m.reply_text(
                f"{_RTL}ℹ️ برای ثبت فیش ابتدا <code>/txin</code> یا <code>/txout</code> بزنید.",
                parse_mode=ParseMode.HTML,
            )
            raise ApplicationHandlerStop
        return

    if m.text and (m.text or "").strip().startswith("/"):
        return
    raw = (m.caption or m.text or "").strip()
    if not has_media and not _text_looks_like_iran_tx_paste(raw):
        return

    # If admin sent a receipt image or tx-like text, try AI then OCR.
    vision_payload: dict | None = None
    if not raw and has_media:
        status_msg = None
        path = None
        source = ""
        try:
            use_ai = receipt_vision_available()
            if receipt_vision_uses_ollama():
                from config.settings import RECEIPT_VISION_TIMEOUT_SEC

                mins = max(1, int(float(RECEIPT_VISION_TIMEOUT_SEC) // 60))
                slow_hint = (
                    f" (Ollama روی CPU؛ تا ~{mins} دقیقه — Vision و OCR هم‌زمان)"
                )
            else:
                slow_hint = ""
            status_msg = await m.reply_text(
                f"{_RTL}⏳ در حال خواندن فیش"
                f"{' با AI' if use_ai else ''}{slow_hint}…",
                parse_mode=ParseMode.HTML,
            )
            path = await _download_receipt_to_temp(context.bot, m)
            if not path:
                await m.reply_text(
                    "❌ دانلود عکس ناموفق بود. دوباره بفرستید یا به‌صورت فایل (Document)."
                )
                return
            logger.info(
                "iran_panel: read start path=%s size=%s ai=%s",
                path,
                os.path.getsize(path),
                use_ai,
            )
            vision_payload, raw, source = await _read_receipt_image(path, mode)
            logger.info(
                "iran_panel: read source=%s amount=%s jdate=%s preview=%r",
                source,
                (vision_payload or {}).get("iran_amount"),
                (vision_payload or {}).get("jdate"),
                (raw or "")[:100],
            )
            if source == "timeout":
                extra = ""
                if receipt_vision_available() and receipt_vision_uses_ollama():
                    extra = (
                        "\n\nℹ️ OCR مبلغ را نخواند — "
                        "<code>apt install tesseract-ocr-fas tesseract-ocr-eng</code>"
                    )
                elif receipt_vision_available():
                    from config.settings import RECEIPT_VISION_MODEL

                    extra = (
                        f"\n\nℹ️ اگر در لاگ <code>api 400</code> دیدید، مدل را عوض کنید: "
                        f"<code>RECEIPT_VISION_MODEL=gpt-4o-mini</code> "
                        f"(فعلی: <code>{RECEIPT_VISION_MODEL}</code>)"
                    )
                await m.reply_text(
                    f"{_RTL}⏱️ خواندن فیش طول کشید. دوباره بفرستید یا متن:\n"
                    f"<code>مبلغ: 494900000\nتاریخ: 1405/03/07\n"
                    f"نام: حسن نصیری\nبانک مقصد: صادرات</code>"
                    f"{extra}",
                    parse_mode=ParseMode.HTML,
                )
                return
            if vision_payload is None and not raw:
                await m.reply_text(
                    "ℹ️ فیش خوانده نشد.\n"
                    "متن بفرستید یا کلید <code>OPENAI_API_KEY</code> را در .env بررسی کنید.",
                    parse_mode=ParseMode.HTML,
                )
                return
        except Exception:
            logger.exception("iran_panel receipt read failed")
            await m.reply_text("❌ خطا در خواندن فیش. دوباره امتحان کنید یا مبلغ را متنی بفرستید.")
            return
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

    if not raw and not vision_payload:
        await m.reply_text("❌ متن خالی است. حداقل مبلغ را بفرستید.")
        return

    if vision_payload is not None:
        payload = vision_payload
    elif mode == "in":
        payload, _err = _parse_payload_in(raw)
    else:
        payload, _err = _parse_payload_out(raw)

    if (m.photo or m.document) and int(payload.get("iran_amount") or 0) < _MIN_RECEIPT_RIAL:
        logger.warning("iran_panel: amount still missing after read")

    # Store draft and ask for missing fields if needed, otherwise allow submit.
    context.user_data[_DRAFT_KEY] = payload
    context.user_data[_DRAFT_MODE_KEY] = mode
    context.user_data.pop(_KEY, None)  # exit raw mode
    context.user_data[_AWAIT_FIELD_KEY] = ""

    _track_tx_message(context, m.message_id)
    if (m.photo or m.document) and _draft_missing_fields(payload, mode):
        missing = _draft_missing_fields(payload, mode)
        lines = [f"{_field_label(f, mode)}: …" for f in missing[:4]]
        if payload.get("iran_amount"):
            lines.insert(0, f"مبلغ: {int(payload['iran_amount'])}")
        if payload.get("jdate"):
            lines.append(f"تاریخ: {payload['jdate']}")
        hint_ai = ""
        if not receipt_vision_available():
            hint_ai = "برای خواندن بهتر فیش، <code>OPENAI_API_KEY</code> در .env بگذارید.\n"
        elif int(payload.get("iran_amount") or 0) < _MIN_RECEIPT_RIAL:
            hint_ai = (
                "مبلغ/تاریخ خوانده نشد — متن بفرستید: "
                "<code>مبلغ: 570000000\nتاریخ: 1405/03/07</code>\n"
                "یا در .env مدل قوی‌تر: <code>RECEIPT_VISION_MODEL=gpt-4o</code>\n"
            )
        await m.reply_text(
            f"{_RTL}⚠️ <b>برخی فیلدها خالی ماند</b> — «ویرایش فیلدها» یا متن:\n"
            f"{hint_ai}"
            f"<code>{chr(10).join(lines) or 'مبلغ: 287625000'}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _track_tx_message(context, m.message_id)
    await _show_draft(context.bot, m.chat_id, context, mode, payload)
    raise ApplicationHandlerStop


async def iran_panel_tx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user or not q.message:
        return
    if not _is_admin(q.from_user.id):
        return
    data = (q.data or "").strip()
    if not data.startswith("tx|"):
        return

    action = data.split("|", 1)[1] if "|" in data else ""
    chat_id = q.message.chat_id

    if action == "cancel":
        await _return_to_main_menu_after_tx_abort(
            context.bot, chat_id, q.from_user.id, context
        )
        try:
            await q.answer("بازگشت به منوی اصلی")
        except Exception:
            pass
        raise ApplicationHandlerStop

    try:
        await q.answer()
    except Exception:
        pass

    mode = context.user_data.get(_DRAFT_MODE_KEY)
    payload = context.user_data.get(_DRAFT_KEY)
    if mode not in ("in", "out") or not isinstance(payload, dict):
        await q.answer("پیش‌نویس پیدا نشد. دوباره /txin یا /txout بزنید.", show_alert=True)
        raise ApplicationHandlerStop

    if action == "submit":
        missing = _draft_missing_fields(payload, mode)
        if missing:
            await q.answer(
                "اول فیلدهای ناقص را از «ویرایش فیلدها» تکمیل کنید.",
                show_alert=True,
            )
            raise ApplicationHandlerStop
        ok, msg = post_transaction(
            base_url=IRAN_PANEL_BASE_URL,
            payload=_panel_payload_for_submit(payload, mode),
        )
        if not ok:
            try:
                await q.answer(f"❌ خطا: {msg}", show_alert=True)
            except Exception:
                pass
            raise ApplicationHandlerStop
        await _cleanup_tx_flow(context.bot, chat_id, context)
        try:
            await q.answer("✅ در سایت ایران ثبت شد.", show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop

    if action == "fill":
        await _show_draft(
            context.bot, chat_id, context, mode, payload, edit_menu=True
        )
        raise ApplicationHandlerStop

    if action == "back":
        context.user_data[_AWAIT_FIELD_KEY] = ""
        await _delete_tx_prompt(context, context.bot, chat_id)
        await _show_draft(context.bot, chat_id, context, mode, payload)
        try:
            await q.answer("بازگشت به پیش‌نویس")
        except Exception:
            pass
        raise ApplicationHandlerStop

    if action.startswith("ef|"):
        field = action.split("|", 1)[1]
        if field not in _fields_for_mode(mode):
            await q.answer("فیلد نامعتبر", show_alert=True)
            raise ApplicationHandlerStop
        context.user_data[_AWAIT_FIELD_KEY] = field
        context.user_data[_DRAFT_KEY] = payload
        await _delete_tx_prompt(context, context.bot, chat_id)
        hint = (
            f"{_RTL}✏️ مقدار جدید «<b>{_field_label(field, mode)}</b>» را بفرستید.\n"
            f"{_RTL}<i>برای خالی گذاشتن (فقط فیلدهای اختیاری):</i> <code>-</code>"
        )
        if field == "iran_amount":
            hint += f"\n{_RTL}مثال: <code>1999000000</code>"
        if field == "jdate":
            hint += f"\n{_RTL}مثال: <code>1405/03/03</code>"
        pm = await context.bot.send_message(
            chat_id,
            hint,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "◀️ بازگشت به پیش‌نویس", callback_data="tx|back"
                        )
                    ],
                    [InlineKeyboardButton("❌ انصراف", callback_data="tx|cancel")],
                ]
            ),
        )
        context.user_data[_PROMPT_MID_KEY] = pm.message_id
        _track_tx_message(context, pm.message_id)
        raise ApplicationHandlerStop


async def iran_panel_fill_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    After clicking "fill", we ask admin to type missing fields.
    """
    if not update.effective_user or not update.message:
        return
    field = (context.user_data.get(_AWAIT_FIELD_KEY) or "").strip()
    if not field:
        return
    from utils.flow_guards import user_ad_flow_active

    if user_ad_flow_active(context):
        return
    if not _is_admin(update.effective_user.id):
        return
    if update.message and _is_tx_cancel_message(update.message.text or ""):
        if await abort_iran_tx_flow_if_active(update, context):
            raise ApplicationHandlerStop
    mode = context.user_data.get(_DRAFT_MODE_KEY)
    payload = context.user_data.get(_DRAFT_KEY)
    if mode not in ("in", "out") or not isinstance(payload, dict):
        context.user_data[_AWAIT_FIELD_KEY] = ""
        return
    val = (update.message.text or "").strip()
    if val == "-":
        val = ""

    if field == "iran_amount":
        amt = _parse_amount_rial(val) or _extract_best_amount(val)
        amt = _normalize_receipt_amount(amt)
        if amt < _MIN_RECEIPT_RIAL:
            await update.message.reply_text(
                f"{_RTL}❌ مبلغ نامعتبر است. مبلغ حواله را به <b>ریال</b> بفرستید "
                f"(مثال: <code>1999000000</code>)."
            )
            raise ApplicationHandlerStop
        payload["iran_amount"] = amt
    elif field == "jdate":
        jd = _normalize_jdate(val) or _extract_jdate(val)
        if not jd:
            await update.message.reply_text(
                f"{_RTL}❌ تاریخ نامعتبر. فرمت: <code>1405/03/03</code>"
            )
            raise ApplicationHandlerStop
        payload["jdate"] = jd
    elif field == "bank_name":
        payload["bank_name"] = _normalize_bank_input(val)
    elif field == "depositor_name":
        payload["depositor_name"] = val
    elif field == "dest_bank":
        payload["dest_bank"] = _normalize_bank_input(val)
    elif field == "transfer_type":
        payload["transfer_type"] = val
    elif field == "description":
        payload["description"] = val
    else:
        await update.message.reply_text(f"{_RTL}❌ فیلد نامعتبر.")
        raise ApplicationHandlerStop

    context.user_data[_DRAFT_KEY] = payload
    context.user_data[_AWAIT_FIELD_KEY] = ""
    _track_tx_message(context, update.message.message_id)
    await _delete_tx_prompt(context, context.bot, update.effective_chat.id)
    await _show_draft(
        context.bot, update.effective_chat.id, context, mode, payload
    )
    raise ApplicationHandlerStop

