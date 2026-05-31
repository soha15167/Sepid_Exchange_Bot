"""
utils/bonbast_rates.py — Fetch & format Bonbast rates / نرخ Bonbast

EN: Loads bonbast.com homepage, POSTs /json with session cookie, formats channel HTML.
FA: دریافت نرخ بازار آزاد از بن‌بست و قالب‌بندی پیام کانال.

Note: Unofficial scrape of public page; for production API see bonbast.com/webmaster.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
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
    if val is None:
        return "—"
    s = str(val).strip().replace(",", "")
    if not s:
        return "—"
    try:
        if "." in s:
            n = float(s)
            if n >= 1000:
                return f"{int(round(n)):,}"
            return s
        return f"{int(s):,}"
    except (TypeError, ValueError):
        return str(val)


def format_bonbast_channel_html(
    data: dict[str, Any],
    *,
    currency_codes: list[str] | None = None,
) -> str:
    """
    EN: HTML message for Telegram channel (sell=1, buy=2 columns on Bonbast).
    FA: پیام HTML برای کانال؛ ستون فروش = {code}1 ، خرید = {code}2.
    """
    codes = currency_codes or list(CURRENCY_LABELS.keys())
    lines = ["📊 <b>نرخ ارز بازار آزاد (تومان)</b>", ""]

    for code in codes:
        c = code.lower().strip()
        sell_k, buy_k = f"{c}1", f"{c}2"
        sell = data.get(sell_k)
        buy = data.get(buy_k)
        if sell is None and buy is None:
            continue
        flag, name = CURRENCY_LABELS.get(c, ("💱", c.upper()))
        lines.append(
            f"{flag} <b>{name}</b>: "
            f"<b>{_fmt_toman(sell)}</b> | <b>{_fmt_toman(buy)}</b>"
        )

    lines.append('📎 منبع: <a href="https://www.bonbast.com">bonbast.com</a>')

    from utils.channel_format import format_channel_ad_footer

    footer = format_channel_ad_footer(rate_toman=0)
    # Footer starts with \n\n (boundary block). For rates post we want only one blank
    # line between source and bot/channel lines.
    if footer.startswith("\n\n"):
        footer = footer[1:]
    return "\n".join(lines) + footer
