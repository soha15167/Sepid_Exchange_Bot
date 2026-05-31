"""
utils/pagination.py — List pagination helpers / صفحه‌بندی لیست‌ها
"""

from __future__ import annotations

from config.settings import LIST_RECENT_LIMIT


def clamp_page(page: int, total_items: int, *, per_page: int = LIST_RECENT_LIMIT) -> tuple[int, int]:
    """Returns (page, total_pages). page is 0-based."""
    per = max(1, int(per_page))
    total = max(0, int(total_items))
    pages = max(1, (total + per - 1) // per) if total else 1
    p = max(0, min(int(page), pages - 1))
    return p, pages


def sql_offset(page: int, *, per_page: int = LIST_RECENT_LIMIT) -> tuple[int, int]:
    per = max(1, int(per_page))
    off = max(0, int(page)) * per
    return per, off


def pagination_nav_row(
    *,
    prev_cb: str | None,
    next_cb: str | None,
    page: int,
    total_pages: int,
) -> list:
    from telegram import InlineKeyboardButton

    row: list = []
    if prev_cb and page > 0:
        row.append(InlineKeyboardButton("◀️ قبلی", callback_data=prev_cb))
    row.append(
        InlineKeyboardButton(
            f"📄 {page + 1}/{total_pages}",
            callback_data="pg|noop",
        )
    )
    if next_cb and page < total_pages - 1:
        row.append(InlineKeyboardButton("بعدی ▶️", callback_data=next_cb))
    return row
