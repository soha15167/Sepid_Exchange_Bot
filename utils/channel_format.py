"""قالب‌بندی مشترک متن آگهی کانال."""

from __future__ import annotations

import html as html_module

_RTL = "\u200f"
_VSEP = f" {_RTL}│{_RTL} "


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
