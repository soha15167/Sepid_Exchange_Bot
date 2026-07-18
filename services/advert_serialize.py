"""Serialize euro_adverts rows for web API (channel-parity fields)."""

from __future__ import annotations

from config.settings import CHANNEL_USERNAME
from database.db import user_advert_has_active_offers
from services.advert_public_offers import list_public_offers_for_advert
from utils.channel_format import country_label_for_advert, format_payment_methods_rtl
from utils.euro_fees import advert_fee_override_eur, format_fee_eur


def _parse_methods(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [m.strip() for m in str(raw).split(",") if m.strip()]


def _advert_type_label(adv: dict) -> str:
    operation = (adv.get("operation") or "").strip()
    euro_ex = int(adv.get("euro_exchange") or 0) == 1
    if operation == "معاوضه" or (euro_ex and operation in ("خرید", "فروش")):
        return "معاوضه یورو"
    if operation == "خرید":
        return "خرید یورو"
    if operation == "فروش":
        return "فروش یورو"
    return operation or "—"


def _methods_section_label(operation: str, *, is_exchange: bool) -> str:
    if is_exchange:
        return "روش دریافت/تحویل"
    return "روش‌های دریافت" if operation == "خرید" else "روش‌های پرداخت"


def serialize_advert_for_web(adv: dict, *, viewer_id: int | None = None) -> dict:
    rid = int(adv.get("rowid") or adv.get("advert_rowid") or adv.get("id") or 0)
    owner_id = int(adv.get("user_id") or 0)
    operation = (adv.get("operation") or "").strip()
    euro_ex = int(adv.get("euro_exchange") or 0) == 1
    is_legacy_exchange = operation == "معاوضه"
    is_exchange = is_legacy_exchange or (euro_ex and operation in ("خرید", "فروش"))

    try:
        euro_amount = int(adv.get("euro_amount") or 0)
    except (TypeError, ValueError):
        euro_amount = 0
    try:
        rate_toman = int(adv.get("rate_toman") or 0)
    except (TypeError, ValueError):
        rate_toman = 0

    methods_list = _parse_methods(adv.get("methods"))
    fee_ov = advert_fee_override_eur(adv)
    fee_display = format_fee_eur(euro_amount if euro_amount > 0 else None, fee_ov)

    ch_mid = adv.get("channel_message_id")
    channel_link = (
        f"https://t.me/{CHANNEL_USERNAME}/{ch_mid}" if ch_mid else None
    )

    owner_name = (
        (adv.get("owner_name") or adv.get("full_name") or "").strip() or "—"
    )

    country_raw = (adv.get("account_country") or "").strip()
    country_label = ""
    if country_raw and country_raw not in ("—", "-", "–"):
        country_label = country_label_for_advert(
            adv, operation=operation, euro_exchange=is_exchange
        )

    instant = (adv.get("instant_transfer") or "").strip() or None
    show_instant = bool(instant) and operation != "خرید" and not is_exchange

    offer_block = list_public_offers_for_advert(rid)

    return {
        "id": rid,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "operation": operation,
        "advert_type": _advert_type_label(adv),
        "is_exchange": is_exchange,
        "euro_amount": euro_amount,
        "rate_toman": rate_toman if not is_exchange else None,
        "fee_eur": fee_display,
        "description": (adv.get("description") or "").strip() or "—",
        "methods": methods_list,
        "methods_label": _methods_section_label(operation, is_exchange=is_exchange),
        "methods_display": format_payment_methods_rtl(methods_list, html=False),
        "account_country": country_raw or None,
        "country_label": country_label or None,
        "instant_transfer": instant if show_instant else None,
        "city_ir": (adv.get("city_ir") or "").strip() or None,
        "city_int": (adv.get("city_int") or "").strip() or None,
        "euro_exchange": euro_ex,
        "status": adv.get("status") or "فعال",
        "created_at": adv.get("created_at"),
        "channel_link": channel_link,
        "channel_message_id": ch_mid,
        "locked": user_advert_has_active_offers(rid) if rid else False,
        "is_mine": viewer_id is not None and owner_id == viewer_id,
        "public_offers": offer_block["items"],
        "offers_completed": offer_block["completed"],
    }
