from __future__ import annotations

import re

from fastapi import APIRouter

from handlers.channel_info import channel_rules_html, fee_schedule_html
from keyboards.menus import (
    CHANNEL_RULES_INLINE_BUTTON_TEXT,
    FEE_INFO_INLINE_BUTTON_TEXT,
    get_payment_selection_text,
)
from services.payment_methods import payment_methods_config

router = APIRouter(prefix="/info", tags=["info"])


def _html_to_plain(html: str) -> str:
    t = re.sub(r"<code>(.*?)</code>", r"\1", html, flags=re.DOTALL)
    t = re.sub(r"</?b>", "", t)
    t = re.sub(r"</?i>", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("\u200f", "").strip()


@router.get("/rules")
def channel_rules():
    return {
        "title": CHANNEL_RULES_INLINE_BUTTON_TEXT.replace("📜 ", ""),
        "text": _html_to_plain(channel_rules_html()),
    }


@router.get("/fees")
def fee_schedule():
    return {
        "title": FEE_INFO_INLINE_BUTTON_TEXT.replace("🧾 ", ""),
        "text": _html_to_plain(fee_schedule_html()),
    }


@router.get("/menu")
def bot_menu_parity():
    """Labels matching keyboards/menus.py main menu."""
    pm = payment_methods_config()
    return {
        "menu_items": [
            {"id": "services", "label": "🚀 درخواست خدمات", "href": "/dashboard/new-advert"},
            {"id": "profile", "label": "🧾 مشاهده پروفایل", "href": "/dashboard/profile"},
            {"id": "offers", "label": "📋 پیشنهادهای من", "href": "/dashboard/offers"},
            {"id": "adverts", "label": "📰 آگهی‌های من", "href": "/dashboard#my-adverts"},
        ],
        "payment_options": pm["payment_options"],
        "payment_hint_buy": get_payment_selection_text("خرید"),
        "payment_hint_sell": get_payment_selection_text("فروش"),
    }
