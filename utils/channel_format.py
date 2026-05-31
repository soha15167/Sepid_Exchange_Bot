"""
utils/channel_format.py — Channel post formatting / قالب متن کانال

EN: RTL payment methods (2-col or pipe-separated); country label by ad type.
FA: چیدمان روش‌های پرداخت؛ برچسب کشور برای خرید/فروش یا معاوضه.
"""

from __future__ import annotations

import html as html_module

_RTL = "\u200f"
_LRI = "\u2066"  # isolate @username LTR inside RTL (Telegram mobile)
_PDI = "\u2069"
_VSEP = f" {_RTL}│{_RTL} "

# Invisible boundary: offer lists are inserted immediately before the footer (ربات/کانال).
CHANNEL_OFFERS_BOUNDARY = "\u2063"
CHANNEL_AD_FOOTER_MARKER = CHANNEL_OFFERS_BOUNDARY
CHANNEL_POST_TEMPLATE_VERSION = 2


def bot_maintenance_channel_notice_html() -> str:
    """Shown on channel posts while bot is disabled."""
    return f"{_RTL}⛔️ <i>ثبت پیشنهاد موقتاً بسته است.</i>\n"


def is_euro_to_euro_advert(advert: dict | None = None, *, operation: str | None = None, euro_exchange: bool = False) -> bool:
    op = (operation or (advert or {}).get("operation") or "").strip()
    ex = euro_exchange or bool(int((advert or {}).get("euro_exchange") or 0) == 1)
    return op == "معاوضه" or (ex and op in ("خرید", "فروش"))


def country_label_for_advert(
    advert: dict | None = None, *, operation: str | None = None, euro_exchange: bool = False
) -> str:
    return "کشور (خارج از ایران)" if is_euro_to_euro_advert(advert, operation=operation, euro_exchange=euro_exchange) else "کشور"


def format_country_display_line(
    account_country_raw,
    advert: dict | None = None,
    *,
    html: bool = True,
    operation: str | None = None,
    euro_exchange: bool = False,
) -> str:
    c = (account_country_raw or "").strip()
    if not c or c in ("—", "-", "–"):
        return ""
    label = country_label_for_advert(advert, operation=operation, euro_exchange=euro_exchange)
    val = html_module.escape(c, quote=False) if html else c
    return f"🗺️ <b>{label}:</b> {val}\n"


def format_payment_methods_rtl(methods: list[str], *, html: bool = False) -> str:
    """روش‌های پرداخت/دریافت؛ با بیش از ۳ گزینه: دو ستون با جداکنندهٔ │."""
    items = [str(m).strip() for m in (methods or []) if m and str(m).strip()]
    if not items:
        return f"{_RTL}—" if html else "\u200f• ندارد"

    def _name(raw: str) -> str:
        return html_module.escape(raw, quote=False) if html else raw

    if len(items) > 3:
        lines: list[str] = []
        for i in range(0, len(items), 2):
            left = _name(items[i])
            right = _name(items[i + 1]) if i + 1 < len(items) else ""
            if right:
                lines.append(f"\u202b{_RTL}{left}{_VSEP}{right}\u202c")
            else:
                lines.append(f"\u202b{_RTL}{left}\u202c")
        return "\n".join(lines)

    return "\n".join(
        f"\u202b{_RTL}• {_name(x)}\u202c" for x in items
    )


def _telegram_at_link_html(username: str) -> str:
    """لینک @username — LRM+LRI تا در RTL موبایل @ چپِ شناسه بماند."""
    u = (username or "").strip().lstrip("@")
    if not u:
        return ""
    esc = html_module.escape(u, quote=False)
    esc_url = html_module.escape(u, quote=True)
    return f'<a href="https://t.me/{esc_url}">\u200e{_LRI}@{esc}{_PDI}</a>'


def format_contact_line_html(label_html: str, username: str) -> str:
    """خط ربات — تراز RTL در موبایل."""
    link = _telegram_at_link_html(username)
    if not link:
        return ""
    return f"\u202b{_RTL}{label_html}\u200f {link}\u202c"


def format_copyable_toman_html(amount: int | float) -> str:
    """مثلاً «10,560,000 تومان» — یک بلوک <code> برای لمس و کپی در تلگرام."""
    try:
        n = f"{int(amount):,}"
    except (TypeError, ValueError):
        n = str(amount)
    label = f"{n} تومان"
    return f"<code>{html_module.escape(label, quote=False)}</code>"


def format_ltr_code_html(value: str) -> str:
    """مقدار LTR داخل <code> — + و @ در متن RTL سمت چپ بمانند."""
    s = str(value or "").strip()
    if not s:
        return "—"
    esc = html_module.escape(s, quote=False)
    return f"<code>\u200e{_LRI}{esc}{_PDI}</code>"


def format_username_bullet_line_html(username: str) -> str:
    link = _telegram_at_link_html(username)
    if not link:
        return ""
    return f"\u202b{_RTL}• یوزرنیم: \u200f {link}\u202c\n"


def format_phone_bullet_line_html(phone: str) -> str:
    s = (phone or "").strip()
    if not s:
        return ""
    return f"\u202b{_RTL}• تلفن: {format_ltr_code_html(s)}\u202c\n"


def format_email_bullet_line_html(email: str) -> str:
    s = (email or "").strip()
    if not s:
        return ""
    return f"\u202b{_RTL}• ایمیل: {format_ltr_code_html(s)}\u202c\n"


def format_channel_line_html(username: str) -> str:
    """خط کانال — لینک t.me (باز شدن کانال، نه حالت کپی مثل code)."""
    link = _telegram_at_link_html(username)
    if not link:
        return ""
    return f"\u202b{_RTL}📢 <b>کانال:</b>\u200f {link}\u202c"


def _footer_spacer_line_html() -> str:
    """خط خالی RTL بعد از کانال — جلوگیری از چسبیدن آیدی به لبهٔ چپ در آخر پاراگراف."""
    return f"\u202b{_RTL}\u00a0\u202c"


def _footer_contact_line(label_html: str, username: str) -> str:
    return format_contact_line_html(label_html, username)


def format_channel_ad_footer(
    *,
    rate_toman: int | None = None,
    bot_username: str | None = None,
    channel_username: str | None = None,
    euro_exchange_no_rate: bool = False,
) -> str:
    """
    EN: Footer only — bot + channel (rate stays in main ad body).
    FA: پایین آگهی: فقط ربات و کانال؛ پیشنهادها قبل از مرز نامرئی درج می‌شوند.
    """
    from config.settings import BOT_USERNAME, CHANNEL_USERNAME

    _ = rate_toman  # rate is shown in ad body, not footer
    lines: list[str] = []
    if euro_exchange_no_rate:
        lines.append(f"\u202b{_RTL}💰 <b>نرخ:</b> معاوضهٔ یورو به یورو\u202c")

    bot = (bot_username or BOT_USERNAME or "").strip().lstrip("@")
    ch = (channel_username or CHANNEL_USERNAME or "").strip().lstrip("@")
    if bot:
        lines.append(_footer_contact_line("🤖 <b>ربات:</b>", bot))
    if ch:
        lines.append(format_channel_line_html(ch))
        lines.append(_footer_spacer_line_html())
    if not lines:
        return ""
    return f"\n\n{CHANNEL_OFFERS_BOUNDARY}\n" + "\n".join(lines) + "\n"
