"""
handlers/registration.py — User signup / ثبت‌نام کاربر

EN: ConversationHandler — name, display name, email, address, phone, SMS code.
FA: فلو چندمرحله‌ای تا ذخیره در جدول users؛ نام نمایشی یکتا در آگهی.
"""

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)
from config.settings import ADMIN_IDS
from utils.validators import is_valid_email, is_valid_phone, normalize_phone_input
from utils.sms import send_verification_sms, generate_sms_code
from database.db import (
    get_user_by_id,
    get_user_by_phone,
    save_user,
    get_user,
    display_name_exists,
    get_restriction_block_message,
)
from models.enums import UserState
from state import user_data_store
from utils.telegram_utils import send_or_replace_main_menu, send_registration_welcome

REGISTER_FULLNAME, REGISTER_LASTNAME, REGISTER_DISPLAY_NAME, REGISTER_EMAIL, REGISTER_ADDRESS, REGISTER_PHONE, VERIFY_CODE = range(7)


async def terms_accept_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورود به فلو ثبت‌نام با اینلاین «قبول قوانین» (callback؛ بدون update.message)."""
    q = update.callback_query
    if not q or (q.data or "") != "terms_accept":
        return ConversationHandler.END
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})

    if user_id not in set(ADMIN_IDS or []):
        block = get_restriction_block_message(user_id)
        if block:
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
                chat_id=q.message.chat_id if q.message else update.effective_chat.id,
                user_id=user_id,
                store=user_data_store,
                text=block,
            )
            return ConversationHandler.END

    if get_user_by_id(user_id) or get_user(user_id):
        try:
            await q.answer()
        except Exception:
            pass
        cid = update.effective_chat.id
        if q.message:
            cid = q.message.chat_id
            try:
                await q.message.delete()
            except Exception:
                pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=cid,
            user_id=user_id,
            store=user_data_store,
            text="ℹ️ شما قبلاً ثبت‌نام کرده‌اید.",
        )
        context.user_data["state"] = UserState.MAIN_MENU.name
        return ConversationHandler.END

    try:
        await q.answer()
    except Exception:
        pass
    context.user_data["registration_active"] = True
    try:
        if q.message:
            await q.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👤 لطفاً نام خود را وارد کنید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REGISTER_FULLNAME


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_user_by_id(user_id) or get_user(user_id):
        await update.message.reply_text("✅ شما قبلاً ثبت‌نام کرده‌اید.")
        return ConversationHandler.END

    await update.message.reply_text("👤 لطفاً نام خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
    return REGISTER_FULLNAME

async def get_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['full_name'] = update.message.text.strip()
    await update.message.reply_text("👤 حالا نام خانوادگی خود را وارد کنید:")
    return REGISTER_LASTNAME

async def get_lastname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['last_name'] = update.message.text.strip()
    await update.message.reply_text("🏷️ لطفاً نام ظاهر شده در آگهی را وارد کنید:")
    return REGISTER_DISPLAY_NAME


async def get_display_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    display_name = (update.message.text or "").strip()
    if not display_name:
        await update.message.reply_text("❌ لطفاً یک نام معتبر وارد کنید:")
        return REGISTER_DISPLAY_NAME
    if display_name_exists(display_name):
        await update.message.reply_text("❌ این نام قبلاً استفاده شده است. لطفاً یک نام دیگر وارد کنید:")
        return REGISTER_DISPLAY_NAME
    context.user_data["display_name"] = display_name
    await update.message.reply_text("📧 لطفاً آدرس ایمیل خود را وارد کنید:")
    return REGISTER_EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if not is_valid_email(email):
        await update.message.reply_text("❌ آدرس ایمیل نامعتبر است. لطفاً دوباره وارد کنید:")
        return REGISTER_EMAIL
    context.user_data['email'] = email
    await update.message.reply_text("🏠 لطفاً آدرس محل زندگی خود را وارد کنید:")
    return REGISTER_ADDRESS

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['address'] = update.message.text.strip()
    await update.message.reply_text("📱 لطفاً شماره موبایل خود را وارد کنید (با + شروع شود):")
    return REGISTER_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = normalize_phone_input(update.message.text or "")
    if not is_valid_phone(phone):
        await update.message.reply_text(
            "❌ شماره معتبر نیست. مثال: <code>+98912xxxxxxx</code> یا <code>0912xxxxxxx</code>\n"
            "فقط ارقام و در صورت نیاز <b>+</b> بفرستید.",
            parse_mode="HTML",
        )
        return REGISTER_PHONE

    if get_user_by_phone(phone):
        await update.message.reply_text("❌ این شماره قبلاً استفاده شده است.")
        uid = update.effective_user.id
        await send_registration_welcome(
            context.bot,
            chat_id=update.effective_chat.id,
            user_id=uid,
            store=user_data_store,
        )
        return ConversationHandler.END

    context.user_data['phone_number'] = phone
    code = generate_sms_code()
    context.user_data['sms_code'] = code

    sent = send_verification_sms(phone, code)
    if sent:
        await update.message.reply_text(
            "📨 یک کد تأیید به شمارهٔ شما ارسال شد. لطفاً آن را در همینجا وارد کنید:"
        )
    else:
        # Twilio خراب / ایران / trial — ثبت‌نام را با کد داخل تلگرام پیش می‌بریم
        await update.message.reply_text(
            "⚠️ ارسال پیامک انجام نشد (تنظیمات Twilio یا محدودیت اپراتور).\n"
            "کد تأیید را در همین چت برایتان فرستادیم؛ همان را وارد کنید:",
        )
        await update.message.reply_text(
            f"🔐 کد تأیید شما: <code>{code}</code>",
            parse_mode="HTML",
        )
    return VERIFY_CODE

async def verify_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_code = update.message.text.strip()
    if input_code != context.user_data.get('sms_code'):
        await update.message.reply_text("❌ کد اشتباه است. لطفاً دوباره تلاش کنید:")
        return VERIFY_CODE

    uid = update.effective_user.id
    cid = update.effective_chat.id
    save_user(
        user_id=uid,
        full_name=context.user_data['full_name'],
        last_name=context.user_data['last_name'],
        email=context.user_data['email'],
        address=context.user_data['address'],
        phone_number=context.user_data['phone_number'],
        display_name=context.user_data.get("display_name"),
        username=update.effective_user.username,
    )
    context.user_data.pop("registration_active", None)
    context.user_data["state"] = UserState.MAIN_MENU.name
    welcome_txt = (
        "✅ ثبت‌نام شما با موفقیت انجام شد.\n\n"
        "📜 قبل از «🚀 ثبت درخواست خدمات»، یک‌بار از منوی پایین گزینهٔ "
        "«قوانین و روال کار کانال» را باز کنید و مطالعه کنید؛ بعد از آن "
        "امکان ثبت درخواست برای شما فعال می‌شود."
    )
    await send_or_replace_main_menu(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
        text=welcome_txt,
    )
    return ConversationHandler.END

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ ثبت‌نام لغو شد.", reply_markup=ReplyKeyboardRemove())
    uid = update.effective_user.id
    context.user_data.pop("registration_active", None)
    await send_registration_welcome(
        context.bot,
        chat_id=update.effective_chat.id,
        user_id=uid,
        store=user_data_store,
        context=context,
    )
    return ConversationHandler.END

# هندلر نهایی برای اضافه شدن در main.py
registration_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^ثبت نام$"), start_registration),
        CallbackQueryHandler(terms_accept_entry, pattern="^terms_accept$"),
    ],
    states={
        REGISTER_FULLNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fullname)],
        REGISTER_LASTNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_lastname)],
        REGISTER_DISPLAY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_display_name)],
        REGISTER_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
        REGISTER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
        REGISTER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        VERIFY_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_sms)],
    },
    fallbacks=[CommandHandler("cancel", cancel_registration)]
)
