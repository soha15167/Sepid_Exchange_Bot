# 📁 callbacks.py - نسخه نهایی با جلوگیری قطعی از خطای edit_message و اجرای درست فلو معاوضه

from telegram import Update
from telegram.ext import ContextTypes

from keyboards.menus import (
    generate_inline_keyboard,
    PAYMENT_OPTIONS,
    EXCHANGE_OPTION,
    METHOD_BY_CALLBACK,
    CONFIRM_SELECTION_CALLBACK,
    get_payment_selection_text,
)
from models.enums import UserState
from state import user_data_store
from handlers.exchange_flow import start_exchange_flow
from handlers.euro_flow import ask_account_country, ask_euro_amount
from keyboards.admin_home import admin_home_inline_keyboard
from config.settings import ADMIN_IDS
from utils.telegram_utils import remember_cleanup_id, send_or_replace_main_menu, reset_flow_user_bucket

_EURO_CLEANUP_KEY = "euro_cleanup_message_ids"
_EXCHANGE_CLEANUP_KEY = "exchange_cleanup_message_ids"


# 🎯 هندل کردن کلیک روی دکمه‌های اینلاین
async def handle_payment_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        return
    user_id = query.from_user.id
    choice = query.data or ""

    # مقداردهی اولیه امن
    if user_id not in user_data_store:
        user_data_store[user_id] = {}
    if "methods" not in user_data_store[user_id]:
        user_data_store[user_id]["methods"] = []
    if "operation" not in user_data_store[user_id]:
        user_data_store[user_id]["operation"] = ""

    data = user_data_store[user_id]

    # جلوگیری از اجرای مجدد اگر فلو معاوضه قبلاً فعال شده باشد
    if "exchange_triggered" in context.user_data:
        try:
            await query.answer()
        except Exception:
            pass
        return

    # تأیید بدون انتخاب روش: فقط یک بار answer (نمایش هشدار)
    if choice in ("confirm", CONFIRM_SELECTION_CALLBACK) and not data.get("methods"):
        try:
            await query.answer("لطفاً حداقل یک روش پرداخت انتخاب کنید.", show_alert=True)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass

    # ❌ انصراف کامل
    if choice == "cancel":
        chat_id_cancel = query.message.chat_id if query.message else user_id
        reset_flow_user_bucket(user_data_store, user_id)
        if query.message:
            try:
                await query.message.delete()
            except Exception:
                pass
        if context.user_data.get("admin_post_advert_for") and user_id in set(ADMIN_IDS or []):
            context.user_data.clear()
            context.user_data["state"] = UserState.ADMIN_MENU.name
            return await context.bot.send_message(
                chat_id=chat_id_cancel,
                text="لغو شد.",
                reply_markup=admin_home_inline_keyboard(),
            )
        await send_or_replace_main_menu(
            context.bot,
            chat_id=chat_id_cancel,
            user_id=user_id,
            store=user_data_store,
        )
        context.user_data['state'] = UserState.MAIN_MENU.name
        return

    # ✅ تایید نهایی انتخاب روش پرداخت
    elif choice in ("confirm", CONFIRM_SELECTION_CALLBACK):
        if EXCHANGE_OPTION in data["methods"] and len(data["methods"]) == 1:
            exchange_side = data.get("operation", "")
            # Persist "buy/sell" side for exchange flow wording.
            user_data_store.setdefault(user_id, {})["exchange_side"] = exchange_side

            # Show selected "exchange" method in chat (cleaned up later).
            try:
                label = "روش دریافت" if exchange_side == "خرید" else "روش پرداخت"
                ack = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"✅ {label}: {EXCHANGE_OPTION}",
                )
                remember_cleanup_id(user_data_store, user_id, ack.message_id, _EXCHANGE_CLEANUP_KEY)
            except Exception:
                pass

            preserved_posting = context.user_data.get("admin_post_advert_for")
            context.user_data.clear()
            if preserved_posting:
                context.user_data["admin_post_advert_for"] = preserved_posting
            context.user_data["state"] = UserState.EXCHANGE_INIT.name
            context.user_data["operation"] = "معاوضه"
            context.user_data["exchange_triggered"] = True
            # Remove the selection message to avoid empty bubbles.
            try:
                await query.message.delete()
            except Exception:
                pass
            return await start_exchange_flow(update, context)

        # Show selected methods in chat (will be cleaned up later).
        try:
            selected = data.get("methods", [])
            methods_text = "، ".join(selected)
            op = data.get("operation", "")
            label = "روش‌های دریافت" if op == "خرید" else "روش‌های پرداخت"
            ack = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ {label}: {methods_text}",
            )
            remember_cleanup_id(user_data_store, user_id, ack.message_id, _EURO_CLEANUP_KEY)
        except Exception:
            pass

        context.user_data["state"] = UserState.EURO_AMOUNT.name
        selected_methods = data.get("methods", [])
        context.user_data["methods"] = selected_methods
        context.user_data["operation"] = data.get("operation", "---")
        # Remove the selection message to avoid empty bubbles.
        try:
            await query.message.delete()
        except Exception:
            pass
        # Ask extra fields (account country + instant transfer) for both BUY and SELL.
        context.user_data["state"] = UserState.EURO_ACCOUNT_COUNTRY.name
        return await ask_account_country(update, context)

        return await ask_euro_amount(update, context)

    # Compatibility: support both old label-based callbacks and new token-based callbacks.
    selected_method = METHOD_BY_CALLBACK.get(choice)
    if selected_method is None and choice in set(PAYMENT_OPTIONS + [EXCHANGE_OPTION]):
        selected_method = choice

    # Ignore any unrelated callback data.
    if selected_method is None:
        return

    # ↩️ انتخاب/حذف گزینه‌ها
    if selected_method == EXCHANGE_OPTION:
        if EXCHANGE_OPTION in data["methods"]:
            data["methods"] = []  # اگر از قبل انتخاب شده بود، حذف کن
        else:
            data["methods"] = [EXCHANGE_OPTION]  # فقط خودش باید انتخاب بشه
    else:
        if EXCHANGE_OPTION in data["methods"]:
            data["methods"].remove(EXCHANGE_OPTION)
        if selected_method in data["methods"]:
            data["methods"].remove(selected_method)
        else:
            data["methods"].append(selected_method)

    user_data_store[user_id] = data
    keyboard = generate_inline_keyboard(data["methods"])

    # جلوگیری از خطای تلگرام اگر متن و کیبورد تغییری نکرده باشند
    if (
        query.message
        and query.message.reply_markup
        and keyboard.to_dict() == query.message.reply_markup.to_dict()
    ):
        return

    op = data.get("operation", "")
    await query.edit_message_text(get_payment_selection_text(op), reply_markup=keyboard)


# ✅ هندلر شروع مستقیم فلو معاوضه (در صورت نیاز مستقیم)
async def handle_direct_exchange_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = UserState.EXCHANGE_INIT.name
    context.user_data["operation"] = "معاوضه"
    context.user_data["exchange_triggered"] = True
    await start_exchange_flow(update, context)
