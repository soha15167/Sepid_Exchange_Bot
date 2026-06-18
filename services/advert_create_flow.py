"""Advert creation validation + preview — mirrors euro_flow / exchange_flow."""

from __future__ import annotations

from config.settings import BOT_USERNAME
from database.db import get_user
from services.advert_serialize import serialize_advert_for_web
from services.payment_methods import (
    payment_methods_config,
    payment_selection_hint,
    validate_payment_methods,
)
from utils.channel_format import format_country_display_line, format_payment_methods_rtl
from utils.euro_fees import format_fee_eur

_INSTANT_MAP = {
    "have": "دارم",
    "dont_have": "ندارم",
    "unknown": "اطلاعی ندارم",
}

_EXCHANGE_INSTANT_MAP = {
    "have": "دارم",
    "dont_have": "ندارم",
    "unknown": "اطلاعی ندارم",
}


def get_create_flow_config() -> dict:
    pm = payment_methods_config()
    return {
        "payment_options": pm["payment_options"],
        "payment_layout_rows": pm["layout_rows"],
        "exchange_option": pm["exchange_option"],
        "multi_select": pm["multi_select"],
        "selection_hint_sell": payment_selection_hint("فروش"),
        "selection_hint_buy": payment_selection_hint("خرید"),
        "operations": ["خرید", "فروش"],
        "exchange_side_options": [
            {"value": "خرید", "label": "خرید یورو (معاوضه)"},
            {"value": "فروش", "label": "فروش یورو (معاوضه)"},
        ],
        "exchange_delivery_options": {
            "خرید": [
                {"value": "transfer", "label": "امکان دریافت به حساب دارم"},
                {"value": "in_person", "label": "امکان دریافت حضوری دارم (دریافت حضوری)"},
            ],
            "فروش": [
                {"value": "transfer", "label": "امکان واریز دارم"},
                {"value": "in_person", "label": "امکان واریز ندارم (تحویل حضوری)"},
            ],
        },
    }


def _owner_display(uid: int, user_row: dict | None = None) -> str:
    u = user_row or get_user(uid) or {}
    dn = (u.get("display_name") or "").strip()
    if dn:
        return dn
    full = f"{u.get('full_name', '')} {u.get('last_name', '')}".strip()
    return full or "—"


def validate_euro_advert(
    *,
    user_id: int,
    operation: str,
    euro_amount: int,
    rate_toman: int,
    description: str,
    methods: list[str],
    account_country: str,
    instant_transfer: str | None = None,
) -> tuple[dict | None, str | None]:
    op = (operation or "").strip()
    if op not in ("خرید", "فروش"):
        return None, "نوع آگهی نامعتبر."
    methods_clean, m_err = validate_payment_methods(methods)
    if m_err:
        return None, m_err
    if euro_amount <= 0:
        return None, "مقدار یورو باید بزرگ‌تر از صفر باشد."
    if rate_toman <= 0:
        return None, "نرخ تومان باید بزرگ‌تر از صفر باشد."
    desc = (description or "").strip()
    if len(desc) < 2:
        return None, "توضیحات باید حداقل ۲ کاراکتر باشد."
    country = (account_country or "").strip()
    if len(country) < 2:
        return None, "کشور حساب را وارد کنید."

    instant_fa: str | None = None
    if op == "فروش" and instant_transfer:
        instant_fa = _INSTANT_MAP.get(instant_transfer, instant_transfer)

    uid = int(user_id)
    owner_name = _owner_display(uid)
    draft = {
        "user_id": uid,
        "full_name": owner_name,
        "operation": op,
        "euro_amount": int(euro_amount),
        "rate_toman": int(rate_toman),
        "description": desc,
        "methods": ", ".join(methods_clean),
        "account_country": country,
        "instant_transfer": instant_fa,
        "euro_exchange": 0,
        "status": "فعال",
    }
    preview = build_euro_preview_row(draft, owner_name=owner_name, methods_list=methods_clean)
    return {"draft": draft, "preview": preview}, None


def build_euro_preview_row(
    draft: dict,
    *,
    owner_name: str,
    methods_list: list[str],
) -> dict:
    op = draft["operation"]
    amount = int(draft["euro_amount"])
    rate = int(draft["rate_toman"])
    methods_label = "روش‌های دریافت" if op == "خرید" else "روش‌های پرداخت"
    advert_type = "خرید یورو" if op == "خرید" else "فروش یورو"
    country_html = format_country_display_line(
        draft.get("account_country"),
        operation=op,
        euro_exchange=False,
        html=False,
    )
    instant = draft.get("instant_transfer")
    show_instant = bool(instant) and op == "فروش"

    return {
        "owner_name": owner_name,
        "advert_type": advert_type,
        "operation": op,
        "methods_label": methods_label,
        "methods_display": format_payment_methods_rtl(methods_list, html=False),
        "euro_amount": amount,
        "rate_toman": rate,
        "total_toman": amount * rate,
        "fee_eur": format_fee_eur(amount),
        "country_label": country_html or None,
        "instant_transfer": instant if show_instant else None,
        "description": draft["description"],
        "channel_footer": f"@{BOT_USERNAME or 'Sepid_Exchange'}",
    }


def validate_exchange_advert(
    *,
    user_id: int,
    side: str,
    delivery: str,
    euro_amount: int,
    account_country: str,
    city_ir: str,
    description: str,
    city_int: str | None = None,
    instant_transfer: str | None = None,
) -> tuple[dict | None, str | None]:
    side = (side or "").strip()
    if side not in ("خرید", "فروش"):
        return None, "نوع معاوضه نامعتبر."
    delivery = (delivery or "").strip()
    if delivery not in ("transfer", "in_person"):
        return None, "روش تحویل/دریافت نامعتبر."
    if euro_amount <= 0:
        return None, "مقدار یورو باید بزرگ‌تر از صفر باشد."
    country = (account_country or "").strip()
    if len(country) < 2:
        return None, "کشور حساب را وارد کنید."
    city_ir_clean = (city_ir or "").strip()
    if len(city_ir_clean) < 2:
        return None, "شهر ایران را وارد کنید."
    desc = (description or "").strip()
    if len(desc) < 2:
        return None, "توضیحات باید حداقل ۲ کاراکتر باشد."

    if delivery == "transfer":
        if side == "خرید":
            method = "امکان دریافت به حساب دارم"
        else:
            method = "امکان واریز به حساب دارم"
        city_int_val = "—"
    else:
        method = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
        city_int_val = (city_int or "").strip()
        if len(city_int_val) < 2:
            return None, "شهر خارج از ایران را وارد کنید."

    instant_fa: str | None = None
    show_instant = side == "فروش" and delivery == "transfer"
    if show_instant and instant_transfer:
        instant_fa = _EXCHANGE_INSTANT_MAP.get(instant_transfer, instant_transfer)

    uid = int(user_id)
    owner_name = _owner_display(uid)
    draft = {
        "user_id": uid,
        "full_name": owner_name,
        "operation": "معاوضه",
        "euro_amount": int(euro_amount),
        "rate_toman": 0,
        "description": desc,
        "methods": method,
        "account_country": country,
        "city_ir": city_ir_clean,
        "city_int": city_int_val,
        "instant_transfer": instant_fa if show_instant else None,
        "euro_exchange": 0,
        "status": "فعال",
    }
    preview = build_exchange_preview_row(
        draft,
        owner_name=owner_name,
        side=side,
        method=method,
        show_instant=show_instant,
    )
    return {"draft": draft, "preview": preview}, None


def build_exchange_preview_row(
    draft: dict,
    *,
    owner_name: str,
    side: str,
    method: str,
    show_instant: bool,
) -> dict:
    amount = int(draft["euro_amount"])
    in_person = method in ("دریافت حضوری", "تحویل حضوری")
    return {
        "owner_name": owner_name,
        "advert_type": "معاوضه یورو",
        "operation": "معاوضه",
        "side": side,
        "side_label": "خرید یورو" if side == "خرید" else "فروش یورو",
        "exchange_method": method,
        "delivery_label": "روش دریافت" if side == "خرید" else "روش تحویل",
        "euro_amount": amount,
        "fee_eur": format_fee_eur(amount),
        "account_country": draft.get("account_country"),
        "city_ir": draft.get("city_ir"),
        "city_int": draft.get("city_int") if in_person else None,
        "instant_transfer": draft.get("instant_transfer") if show_instant else None,
        "description": draft["description"],
        "is_exchange": True,
        "channel_footer": f"@{BOT_USERNAME or 'Sepid_Exchange'}",
    }


def draft_to_serialized(draft: dict, *, advert_id: int = 0, viewer_id: int | None = None) -> dict:
    row = dict(draft)
    row["rowid"] = advert_id
    return serialize_advert_for_web(row, viewer_id=viewer_id)
