"""
utils/bonbast_rates.py — Fetch & format Bonbast rates / نرخ Bonbast

EN: Fetches one Bonbast snapshot and formats currencies, gold, and coins together.
FA: دریافت نرخ ارز، طلا و سکه از بن‌بست و قالب‌بندی یک پیام کانال.

Note: Unofficial scrape of public page; for production API see bonbast.com/webmaster.
"""

from __future__ import annotations

import json
import html as html_module
import re
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from typing import Any

_BONBAST_HOME = "https://bonbast.com/"
_BONBAST_JSON = "https://bonbast.com/json"
_PARAM_RE = re.compile(r"\$\.post\('/json',\s*\{param:\s*\"([^\"]+)\"", re.I)
_UA = "Mozilla/5.0 (compatible; SepidExchangeBot/1.0; +https://t.me/Sepid_Exchange)"

# code -> (flag, Persian name) / کد -> (پرچم، نام فارسی)
CURRENCY_LABELS: dict[str, tuple[str, str]] = {
    "usd": ("🇺🇸", "دلار آمریکا"),
    "eur": ("🇪🇺", "یورو"),
    "gbp": ("🇬🇧", "پوند انگلیس"),
    "aed": ("🇦🇪", "درهم امارات"),
    "try": ("🇹🇷", "لیر ترکیه"),
    "chf": ("🇨🇭", "فرانک سوئیس"),
    "cad": ("🇨🇦", "دلار کانادا"),
    "aud": ("🇦🇺", "دلار استرالیا"),
    "sek": ("🇸🇪", "کرون سوئد"),
    "nok": ("🇳🇴", "کرون نروژ"),
    "dkk": ("🇩🇰", "کرون دانمارک"),
    "sar": ("🇸🇦", "ریال عربستان"),
    "qar": ("🇶🇦", "ریال قطر"),
    "kwd": ("🇰🇼", "دینار کویت"),
    "bhd": ("🇧🇭", "دینار بحرین"),
    "omr": ("🇴🇲", "ریال عمان"),
    "rub": ("🇷🇺", "روبل روسیه"),
    "cny": ("🇨🇳", "یوان چین"),
    "jpy": ("🇯🇵", "ین ژاپن"),
    "inr": ("🇮🇳", "روپیه هند"),
    "iqd": ("🇮🇶", "دینار عراق"),
    "afn": ("🇦🇫", "افغانی"),
    "azn": ("🇦🇿", "منات آذربایجان"),
    "myr": ("🇲🇾", "رینگیت مالزی"),
    "sgd": ("🇸🇬", "دلار سنگاپور"),
    "hkd": ("🇭🇰", "دلار هنگ‌کنگ"),
}

# Bonbast coin identifiers are explicit pairs, not currency-style code1/code2.
# In particular, emami12 is the buy price; azadi1_2 is a half coin's sell price.
COIN_QUOTES = (
    ("سکه امامی", "emami1", "emami12"),
    ("سکه بهار آزادی", "azadi1", "azadi12"),
    ("نیم‌سکه", "azadi1_2", "azadi1_22"),
    ("ربع‌سکه", "azadi1_4", "azadi1_42"),
    ("سکه گرمی", "azadi1g", "azadi1g2"),
)
GOLD_QUOTES = (
    ("هر گرم طلای ۱۸ عیار", "gol18", "تومان"),
    ("مثقال طلا", "mithqal", "تومان"),
    ("اونس جهانی طلا", "ounce", "دلار"),
)


def _fetch_html_and_json() -> dict[str, Any]:
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": _UA}

    req = urllib.request.Request(_BONBAST_HOME, headers=headers)
    html = opener.open(req, timeout=15).read().decode("utf-8", errors="replace")
    m = _PARAM_RE.search(html)
    if not m:
        raise RuntimeError("Bonbast: param token not found in homepage")

    data = urllib.parse.urlencode({"param": m.group(1)}).encode()
    req2 = urllib.request.Request(
        _BONBAST_JSON,
        data=data,
        headers={
            **headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": _BONBAST_HOME,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    raw = opener.open(req2, timeout=15).read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    if payload.get("rest") == "1" and len(payload) < 3:
        raise RuntimeError("Bonbast: rate limit or invalid session (rest=1)")
    return payload


def fetch_bonbast_rates() -> dict[str, Any]:
    """EN: Synchronous fetch. FA: دریافت هم‌زمان JSON نرخ‌ها."""
    return _fetch_html_and_json()


def _fmt_toman(val: Any) -> str:
    """Group numeric quotes without rounding away the source's decimal prices."""
    if val is None or isinstance(val, bool):
        return "—"
    s = str(val).strip().replace(",", "")
    if not s or len(s) > 32:
        return "—"
    try:
        number = Decimal(s)
    except InvalidOperation:
        return "—"
    if not number.is_finite() or number <= 0 or number.adjusted() > 20:
        return "—"
    # Bound decimal expansion as well as the input length (e.g. 1e-100000).
    if number.as_tuple().exponent < -12:
        return "—"
    formatted = format(number, ",f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def format_bonbast_channel_html(
    data: dict[str, Any],
    *,
    currency_codes: list[str] | None = None,
) -> str:
    """
    EN: One snapshot, one channel message. Currency filtering leaves metals intact.
    FA: پیام واحد ارز، طلا و سکه؛ قیمت فروش قبل از خرید و واحد اونس دلار است.
    """
    codes = currency_codes or list(CURRENCY_LABELS.keys())
    lines = ["📊 <b>نرخ ارز، طلا و سکه</b>", "ترتیب قیمت‌های دوتایی: فروش | خرید", ""]
    currency_lines = []
    seen = set()

    for code in codes:
        c = code.lower().strip()
        if c in seen:
            continue
        seen.add(c)
        sell_k, buy_k = f"{c}1", f"{c}2"
        sell = _fmt_toman(data.get(sell_k))
        buy = _fmt_toman(data.get(buy_k))
        if sell == buy == "—":
            continue
        flag, name = CURRENCY_LABELS.get(c, ("💱", c.upper()))
        currency_lines.append(
            f"{flag} <b>{html_module.escape(name)}</b>: "
            f"<b>{sell}</b> | <b>{buy}</b>"
        )

    gold_lines = []
    for label, key, unit in GOLD_QUOTES:
        value = _fmt_toman(data.get(key))
        if value != "—":
            gold_lines.append(f"▫️ <b>{label}</b>: <b>{value}</b> {unit}")

    coin_lines = []
    for label, sell_key, buy_key in COIN_QUOTES:
        sell, buy = _fmt_toman(data.get(sell_key)), _fmt_toman(data.get(buy_key))
        if sell != "—" or buy != "—":
            coin_lines.append(f"🪙 <b>{label}</b>: <b>{sell}</b> | <b>{buy}</b>")

    if not any((currency_lines, gold_lines, coin_lines)):
        raise ValueError("Bonbast: no usable currency, gold, or coin prices")
    unavailable = []
    for heading, label, rows in (
        ("💱 ارز (تومان)", "ارز", currency_lines),
        ("🏅 طلا", "طلا", gold_lines),
        ("🪙 سکه (تومان)", "سکه", coin_lines),
    ):
        if rows:
            lines.extend([f"<b>{heading}</b>", *rows, ""])
        else:
            unavailable.append(label)
    if unavailable:
        lines.extend([f"⚠️ نرخ {' و '.join(unavailable)} در پاسخ بن‌بست موجود نیست.", ""])

    lines.append('📎 منبع: <a href="https://www.bonbast.com">bonbast.com</a>')

    from utils.channel_format import format_channel_ad_footer

    footer = format_channel_ad_footer(rate_toman=0)
    # Footer starts with \n\n (boundary block). For rates post we want only one blank
    # line between source and bot/channel lines.
    if footer.startswith("\n\n"):
        footer = footer[1:]
    return "\n".join(lines) + footer
