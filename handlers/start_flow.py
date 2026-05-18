"""
handlers/start_flow.py — Welcome & terms / خوش‌آمد و قوانین

EN: `/start`, auto registration intro + terms, accept/decline callbacks.
FA: شروع ربات، نمایش قوانین، پذیرش/رد؛ ورود به ثبت‌نام پس از پذیرش.
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config.settings import ADMIN_IDS
from database.db import get_restriction_block_message
from keyboards.menus import terms_inline_keyboard
from models.enums import UserState
from messages import texts
from state import user_data_store
from utils.telegram_utils import (
    cleanup_ids,
    remember_cleanup_id,
    send_or_replace_main_menu,
    send_registration_welcome,
)
from handlers.offers import deliver_offer_proposal_gate, parse_offer_start_payload

_MAIN_CLEANUP_KEY = "main_cleanup_message_ids"

# 🟢 مرحله اول - خوش آمدگویی و دکمه شروع
async def handle_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})
    offer_ad_id = parse_offer_start_payload(list(context.args or []))

    if user_id not in set(ADMIN_IDS or []):
        block = get_restriction_block_message(user_id)
        if block:
            context.user_data.clear()
            context.user_data["state"] = UserState.MAIN_MENU.name
            await send_or_replace_main_menu(
                context.bot,
                chat_id=update.effective_chat.id,
                user_id=user_id,
                store=user_data_store,
                text=block,
            )
            try:
                if update.message:
                    await update.message.delete()
            except Exception:
                pass
            return

    if offer_ad_id is not None:
        raw_mids = context.user_data.get("offer_flow_mids")
        mids_to_clean = (
            [int(x) for x in raw_mids if x is not None]
            if isinstance(raw_mids, list)
            else []
        )
        context.user_data.clear()
        try:
            if update.message:
                await update.message.delete()
        except Exception:
            pass
        if mids_to_clean:
            await cleanup_ids(
                context.bot,
                chat_id=update.effective_chat.id,
                ids=mids_to_clean,
            )
        await deliver_offer_proposal_gate(context, user_id, offer_ad_id)
        return

    context.user_data.clear()
    if update.message:
        remember_cleanup_id(user_data_store, user_id, update.message.message_id, _MAIN_CLEANUP_KEY)

    mid = await send_registration_welcome(
        context.bot,
        chat_id=update.effective_chat.id,
        user_id=user_id,
        store=user_data_store,
        context=context,
    )
    remember_cleanup_id(user_data_store, user_id, mid, _MAIN_CLEANUP_KEY)
    context.user_data['state'] = UserState.START.name
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass

# 🟢 نمایش قوانین و پذیرش
async def show_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})

    if user_id not in set(ADMIN_IDS or []):
        block = get_restriction_block_message(user_id)
        if block:
            q = update.callback_query
            cid = update.effective_chat.id
            if q:
                try:
                    await q.answer()
                except Exception:
                    pass
                if q.message:
                    cid = q.message.chat_id
                    try:
                        await q.message.delete()
                    except Exception:
                        pass
            context.user_data.clear()
            context.user_data["state"] = UserState.MAIN_MENU.name
            await send_or_replace_main_menu(
                context.bot,
                chat_id=cid,
                user_id=user_id,
                store=user_data_store,
                text=block,
            )
            return

    if update.message:
        remember_cleanup_id(user_data_store, user_id, update.message.message_id, _MAIN_CLEANUP_KEY)
    if update.callback_query:
        remember_cleanup_id(user_data_store, user_id, update.callback_query.message.message_id if update.callback_query.message else None, _MAIN_CLEANUP_KEY)

    sent = await update.effective_message.reply_text(
        texts.TERMS_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=terms_inline_keyboard,
    )
    remember_cleanup_id(user_data_store, user_id, sent.message_id, _MAIN_CLEANUP_KEY)
    context.user_data['state'] = UserState.TERMS.name

# 🟢 عدم قبول قوانین (قبول توسط ConversationHandler ثبت‌نام با terms_accept هندل می‌شود)
async def handle_terms_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})

    q = update.callback_query
    if not q or (q.data or "") != "terms_decline":
        return

    if user_id not in set(ADMIN_IDS or []):
        block = get_restriction_block_message(user_id)
        if block:
            cid = update.effective_chat.id
            if q.message:
                cid = q.message.chat_id
            try:
                await q.answer()
            except Exception:
                pass
            if q.message:
                try:
                    await q.message.delete()
                except Exception:
                    pass
            context.user_data.clear()
            context.user_data["state"] = UserState.MAIN_MENU.name
            await send_or_replace_main_menu(
                context.bot,
                chat_id=cid,
                user_id=user_id,
                store=user_data_store,
                text=block,
            )
            return

    try:
        await q.answer()
    except Exception:
        pass
    remember_cleanup_id(
        user_data_store,
        user_id,
        q.message.message_id if q.message else None,
        _MAIN_CLEANUP_KEY,
    )
    mid = await send_registration_welcome(
        context.bot,
        chat_id=q.message.chat_id if q.message else update.effective_chat.id,
        user_id=user_id,
        store=user_data_store,
        context=context,
    )
    remember_cleanup_id(user_data_store, user_id, mid, _MAIN_CLEANUP_KEY)
