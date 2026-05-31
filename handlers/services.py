"""
handlers/services.py — Main menu services / منوی خدمات

EN: Entry to buy/sell euro, VPN placeholder, cancel; requires channel rules ack.
FA: درخواست خدمات، خرید/فروش یورو؛ نیاز به مطالعهٔ قوانین کانال.
"""

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from keyboards.admin_home import admin_home_inline_keyboard
from keyboards.menus import (
    services_inline_keyboard,
    generate_inline_keyboard,
    get_payment_selection_text,
)
from config.settings import ADMIN_IDS
from database.db import get_restriction_block_message, get_user
from models.enums import UserState
from state import user_data_store
from handlers.access_gate import ensure_registered_or_redirect
from utils.telegram_utils import safe_delete_message
from utils.telegram_utils import (
    remember_cleanup_id,
    cleanup_ids,
    send_or_replace_main_menu,
    reset_flow_user_bucket,
    cleanup_transient_dm_messages,
)

_EURO_CLEANUP_KEY = "euro_cleanup_message_ids"
_MAIN_CLEANUP_KEY = "main_cleanup_message_ids"
_EXCHANGE_CLEANUP_KEY = "exchange_cleanup_message_ids"

# 📋 نمایش منوی اصلی خدمات
async def show_services_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ensure_registered_or_redirect(update, context):
        return
    user_id = update.effective_user.id
    if user_id not in set(ADMIN_IDS or []):
        block = get_restriction_block_message(user_id)
        if block:
            q = update.callback_query
            if q:
                cid = q.message.chat_id if q.message else update.effective_chat.id
                try:
                    await q.answer()
                except Exception:
                    pass
                if q.message:
                    try:
                        await q.message.delete()
                    except Exception:
                        pass
                await send_or_replace_main_menu(
                    context.bot,
                    chat_id=cid,
                    user_id=user_id,
                    store=user_data_store,
                    text=block,
                )
            elif update.message:
                try:
                    await update.message.delete()
                except Exception:
                    pass
                await send_or_replace_main_menu(
                    context.bot,
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    store=user_data_store,
                    text=block,
                )
            return

    if user_id not in set(ADMIN_IDS or []):
        u = get_user(user_id)
        if u is not None and int(u.get("channel_rules_ack") or 0) == 0:
            q = update.callback_query
            alert = (
                "ابتدا از منوی اصلی گزینهٔ «قوانین و روال کار کانال» را باز کنید و مطالعه کنید؛ "
                "بعد «درخواست خدمات» برای شما فعال می‌شود."
            )
            if q:
                try:
                    await q.answer(alert, show_alert=True)
                except Exception:
                    pass
            elif update.message:
                try:
                    await update.message.reply_text(f"\u200f{alert}")
                except Exception:
                    pass
                try:
                    await update.message.delete()
                except Exception:
                    pass
            return

    user_data_store.setdefault(user_id, {"methods": [], "operation": ""})
    # When entering service request, clear all previous "main menu" messages.
    ids = user_data_store.get(user_id, {}).pop(_MAIN_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=update.effective_chat.id, ids=ids)
    # If invoked from inline main menu, remove that message too.
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass

    msg = await update.effective_message.reply_text(
        "📋 لطفاً یکی از خدمات زیر را انتخاب کنید:",
        reply_markup=services_inline_keyboard
    )
    context.user_data["services_menu_message_id"] = msg.message_id
    # Track both user's click and bot menu message for later cleanup after confirm_advert.
    remember_cleanup_id(user_data_store, user_id, update.message.message_id if update.message else None, _EURO_CLEANUP_KEY)
    remember_cleanup_id(user_data_store, user_id, msg.message_id, _EURO_CLEANUP_KEY)


async def handle_services_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ensure_registered_or_redirect(update, context):
        return
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    user_data_store.setdefault(user_id, {"methods": [], "operation": ""})

    if query.data == "svc_cancel":
        chat_id = query.message.chat_id if query.message else update.effective_chat.id
        ids = []
        bucket = user_data_store.get(user_id, {})
        ids += bucket.pop(_EURO_CLEANUP_KEY, [])
        ids += bucket.pop(_EXCHANGE_CLEANUP_KEY, [])
        ids += bucket.pop(_MAIN_CLEANUP_KEY, [])
        await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
        reset_flow_user_bucket(user_data_store, user_id)
        context.user_data.clear()
        context.user_data["state"] = UserState.MAIN_MENU.name
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=query.message.chat_id,
            user_id=user_id,
            store=user_data_store,
        )
        return

    if query.data == "svc_euro":
        context.user_data['state'] = UserState.SERVICE_SELECTION.name
        # Replace services menu with operation chooser.
        try:
            await query.edit_message_text(
                "لطفا عملیات مورد نظر خود را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🟢 خرید یورو", callback_data="service_op_buy"),
                        InlineKeyboardButton("🔴 فروش یورو", callback_data="service_op_sell"),
                    ],
                    [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")]
                ]),
            )
        except Exception:
            op_msg = await query.message.reply_text(
                "لطفا عملیات مورد نظر خود را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🟢 خرید یورو", callback_data="service_op_buy"),
                        InlineKeyboardButton("🔴 فروش یورو", callback_data="service_op_sell"),
                    ],
                    [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")]
                ]),
            )
            remember_cleanup_id(user_data_store, user_id, op_msg.message_id, _EURO_CLEANUP_KEY)
        return

    if query.data == "svc_vpn":
        # Placeholder: for now just go back to main menu.
        try:
            await query.edit_message_text("ℹ️ این بخش هنوز فعال نشده است.", reply_markup=None)
        except Exception:
            pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=query.message.chat_id,
            user_id=user_id,
            store=user_data_store,
            text="🏠 بازگشت به منوی اصلی:",
        )
        context.user_data["state"] = UserState.MAIN_MENU.name
        return


# ❌ انصراف از عملیات و بازگشت به منوی اصلی
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in set(ADMIN_IDS or []):
        from handlers.iran_panel_sync import abort_iran_tx_flow_if_active

        if await abort_iran_tx_flow_if_active(update, context):
            return
    if context.user_data.get("admin_post_advert_for") and user_id in set(ADMIN_IDS or []):
        user_data_store.pop(user_id, None)
        context.user_data.clear()
        context.user_data["state"] = UserState.ADMIN_MENU.name
        return await update.effective_message.reply_text(
            "لغو شد.",
            reply_markup=admin_home_inline_keyboard(),
        )
    chat_id = update.effective_chat.id
    extra = [update.message.message_id] if update.message else []
    await cleanup_transient_dm_messages(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    reset_flow_user_bucket(user_data_store, user_id)
    context.user_data.clear()
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text="🏠 بازگشت به منوی اصلی:",
    )
    context.user_data["state"] = UserState.MAIN_MENU.name


async def handle_service_operation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ensure_registered_or_redirect(update, context):
        return
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {"methods": [], "operation": ""}

    if query.data == "service_op_buy":
        operation = "خرید"
    elif query.data == "service_op_sell":
        operation = "فروش"
    else:
        return

    if user_id not in set(ADMIN_IDS or []):
        from utils.channel_membership import (
            ensure_advert_channel_member,
            channel_membership_keyboard,
        )

        ok, err = await ensure_advert_channel_member(context.bot, user_id)
        if not ok:
            try:
                await query.answer(
                    "ابتدا عضو کانال شوید، بعد «عضو شدم — بازگشت به منو».",
                    show_alert=True,
                )
            except Exception:
                pass
            kb = channel_membership_keyboard()
            if query.message and err:
                try:
                    sent = await query.message.reply_text(
                        err,
                        parse_mode="HTML",
                        reply_markup=kb,
                        disable_web_page_preview=True,
                    )
                    context.user_data["channel_member_block_mid"] = sent.message_id
                except Exception:
                    pass
            return

    user_data_store[user_id]["operation"] = operation
    user_data_store[user_id]["methods"] = []
    context.user_data['state'] = UserState.SERVICE_SELECTION.name

    await query.edit_message_text(
        get_payment_selection_text(operation),
        reply_markup=generate_inline_keyboard([]),
        parse_mode="HTML",
    )


async def handle_inline_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    if context.user_data.get("admin_post_advert_for") and user_id in set(ADMIN_IDS or []):
        chat_id = query.message.chat_id if query.message else update.effective_chat.id
        ids = []
        ids += list((context.user_data or {}).get("offer_flow_mids") or [])
        bucket = user_data_store.get(user_id, {})
        ids += bucket.pop(_EURO_CLEANUP_KEY, [])
        ids += bucket.pop(_EXCHANGE_CLEANUP_KEY, [])
        ids += bucket.pop(_MAIN_CLEANUP_KEY, [])
        await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
        user_data_store.pop(user_id, None)
        context.user_data.clear()
        context.user_data["state"] = UserState.ADMIN_MENU.name
        try:
            await query.message.delete()
        except Exception:
            pass
        return await context.bot.send_message(
            chat_id=chat_id,
            text="لغو شد.",
            reply_markup=admin_home_inline_keyboard(),
        )
    chat_id = query.message.chat_id if query.message else update.effective_chat.id
    extra = [query.message.message_id] if query.message else []
    await cleanup_transient_dm_messages(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    reset_flow_user_bucket(user_data_store, user_id)
    context.user_data.clear()
    context.user_data['state'] = UserState.MAIN_MENU.name
    try:
        await query.message.delete()
    except Exception:
        pass
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
    )
