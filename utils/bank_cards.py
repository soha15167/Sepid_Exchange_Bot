"""
utils/bank_cards.py — Admin bank cards formatting / قالب کارت‌های بانکی

EN: Build copy-friendly text (card/IBAN inside <code>).
FA: پیام قابل کپی برای ارسال به کاربر (شماره کارت/شبا داخل code).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import html


@dataclass(frozen=True)
class BankCard:
    id: str
    title: str
    card: str = ""
    iban: str = ""


def _bank_icon(title: str) -> str:
    return "🏦"


def display_bank_title(title: str) -> str:
    """
    Convert title like "ملی — حسن نصیری" to "🏛️ بانک ملی — حسن نصیری".
    If title already contains "بانک", we won't duplicate it.
    """
    t = (title or "").strip()
    if not t:
        return ""
    icon = _bank_icon(t)
    if "بانک" in t:
        return f"{icon} {t}"
    if "—" in t:
        left, right = [p.strip() for p in t.split("—", 1)]
        return f"{icon} بانک {left} — {right}"
    return f"{icon} بانک {t}"


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _normalize_iban(s: str) -> str:
    s = (s or "").strip().replace(" ", "").upper()
    return s


def parse_bank_cards(raw: Any) -> list[BankCard]:
    if not isinstance(raw, list):
        return []
    out: list[BankCard] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not cid or not title:
            continue
        card = _digits_only(str(item.get("card") or ""))
        iban = _normalize_iban(str(item.get("iban") or ""))
        out.append(BankCard(id=cid, title=title, card=card, iban=iban))
    return out


def format_bank_card_html(card: BankCard) -> str:
    # Minimal spacing; copy-friendly.
    title = display_bank_title(card.title) or card.title
    lines: list[str] = [f"<b>{html.escape(title)}</b>"]
    if card.card:
        lines.append(f"💳 شماره کارت: <code>{html.escape(card.card)}</code>")
    if card.iban:
        lines.append(f"🔢 شبا: <code>{html.escape(card.iban)}</code>")
    return "\n".join(lines)

