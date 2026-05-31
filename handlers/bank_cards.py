"""
handlers/bank_cards.py — Admin quick-send bank cards / ارسال سریع کارت‌های بانکی
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config.settings import ADMIN_IDS, BANK_CARDS
from database.db import record_dm_trackable_message
from state import user_data_store
from utils.bank_cards import display_bank_title, format_bank_card_html, parse_bank_cards
from utils.telegram_utils import send_or_replace_main_menu

logger = logging.getLogger(__name__)


def _is_admin(uid: int) -> bool:
    return uid in set(ADMIN_IDS or [])


def _cards_keyboard(cards) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for c in cards:
        btn_title = display_bank_title(c.title) or c.title
        pair.append(
            InlineKeyboardButton(f"{btn_title[:28]}", callback_data=f"cards|pick|{c.id}")
        )
        if len(pair) >= 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="cards|home")])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("—", callback_data="cards|noop")]])


async def admin_cards_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _is_admin(update.effective_user.id):
        return
    cards = parse_bank_cards(BANK_CARDS)
    if not cards:
        await update.message.reply_text(
            "❌ هیچ کارت بانکی تنظیم نشده.\n\n"
            "در فایل `.env` مقدار `BANK_CARDS_JSON` را تنظیم کنید و سرویس را ری‌استارت کنید.",
            disable_web_page_preview=True,
        )
        return
    sent = await update.message.reply_text(
        "💳 <b>کارت‌های بانکی</b>\n\nروی هر کارت بزنید تا متنِ قابل کپی ارسال شود.",
        parse_mode=ParseMode.HTML,
        reply_markup=_cards_keyboard(cards),
    )
    # Keep chat clean: delete the /cards command message if possible,
    # and mark the picker message as trackable for later cleanup.
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        record_dm_trackable_message(update.effective_user.id, sent.message_id)
    except Exception:
        pass


async def bank_cards_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message or not q.from_user:
        return
    if not _is_admin(q.from_user.id):
        return
    data = q.data or ""
    if not data.startswith("cards|"):
        return
    try:
        await q.answer()
    except Exception:
        pass
    parts = data.split("|")
    action = parts[1] if len(parts) > 1 else ""
    if action == "noop":
        return
    if action == "home":
        # Remove picker message and show main menu.
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=q.message.chat_id,
            user_id=q.from_user.id,
            store=user_data_store,
            text="🏠 منوی اصلی:",
        )
        return
    if action != "pick" or len(parts) < 3:
        return
    cid = parts[2]
    cards = parse_bank_cards(BANK_CARDS)
    picked = next((c for c in cards if c.id == cid), None)
    if not picked:
        await q.answer("کارت پیدا نشد", show_alert=True)
        return
    text = format_bank_card_html(picked)
    try:
        sent = await q.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        try:
            record_dm_trackable_message(q.from_user.id, sent.message_id)
        except Exception:
            pass
        # Remove picker message to avoid chat clutter.
        try:
            await q.message.delete()
        except Exception:
            pass
        # Return to main menu immediately.
        await send_or_replace_main_menu(
            context.bot,
            chat_id=sent.chat_id,
            user_id=q.from_user.id,
            store=user_data_store,
            text="🏠 منوی اصلی:",
        )
    except Exception:
        logger.exception("bank_cards: send failed")

