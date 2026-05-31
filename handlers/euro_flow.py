"""
handlers/euro_flow.py — Buy/sell euro (Toman rate) / خرید و فروش یورو

EN: Amount, rate, description, country, instant transfer → channel post.
FA: مقدار، نرخ تومان، توضیحات، کشور، واریز آنی → انتشار در کانال.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from models.enums import UserState
from keyboards.admin_home import admin_home_inline_keyboard
from keyboards.menus import (
    inline_cancel_keyboard,
    main_menu_inline_keyboard,
)
from config.settings import ADVERT_CHANNEL_ID, CHANNEL_USERNAME
from utils.channel_format import format_channel_ad_footer
from database.db import get_user, get_db
from state import user_data_store
from utils.telegram_utils import (
    remember_cleanup_id,
    cleanup_ids,
    cleanup_transient_dm_messages,
    mark_flow_keep_message,
    send_or_replace_main_menu,
)
from utils.euro_fees import format_fee_eur as _format_fee_eur
from handlers.offers import channel_ad_reply_markup
from utils.channel_format import format_payment_methods_rtl as _format_methods_rtl
from utils.channel_membership import (
    channel_membership_required_html,
    ensure_advert_channel_member,
    channel_membership_keyboard,
)
from utils.channel_ad_publish import try_open_telegram_url

_EURO_CLEANUP_KEY = "euro_cleanup_message_ids"


def resolve_channel_advert_identity(context: ContextTypes.DEFAULT_TYPE, acting_user_id: int) -> tuple[int, str]:
    """Normal users post for themselves; admins may post for another user with a custom display name."""
    posting = context.user_data.get("admin_post_advert_for") or {}
    target = posting.get("user_id")
    display = (posting.get("display_name") or "").strip()
    if target is not None:
        try:
            owner_id = int(target)
        except (TypeError, ValueError):
            owner_id = acting_user_id
    else:
        owner_id = acting_user_id
    if display:
        return owner_id, display
    db_user = get_user(owner_id) or {}
    full_name = (db_user.get("display_name") or f"{db_user.get('full_name', '')} {db_user.get('last_name', '')}").strip()
    return owner_id, full_name


async def _ack_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send a short ✅ confirmation (cleaned up later)."""
    user_id = update.effective_user.id
    sent = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    remember_cleanup_id(user_data_store, user_id, sent.message_id, _EURO_CLEANUP_KEY)

def _format_optional_line(label: str, value: str | None) -> str:
    if not value:
        return ""
    return f"{label} {value}\n"


def _channel_country_html(
    country_raw: str | None,
    *,
    operation: str = "",
    euro_exchange: bool = False,
) -> str:
    from utils.channel_format import format_country_display_line

    return format_country_display_line(
        country_raw,
        operation=operation,
        euro_exchange=euro_exchange,
        html=True,
    )


def _format_instant_transfer(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {
        "have": "دارم",
        "dont_have": "ندارم",
        "unknown": "اطلاعی ندارم",
    }
    return mapping.get(value, value)

async def ask_euro_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = UserState.EURO_AMOUNT.name
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else user_id
    user_data_store.setdefault(user_id, {})
    prompt = await context.bot.send_message(
        chat_id=chat_id,
        text="💶 لطفاً مقدار یورو مورد نظر را وارد کنید\n(مثال: 1200)",
        reply_markup=inline_cancel_keyboard(),
    )
    context.user_data["last_prompt_message_id"] = prompt.message_id
    remember_cleanup_id(user_data_store, user_id, prompt.message_id, _EURO_CLEANUP_KEY)


async def ask_account_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only used for SELL flow after payment method confirmation.
    context.user_data['state'] = UserState.EURO_ACCOUNT_COUNTRY.name
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})
    if update.callback_query:
        prompt = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🌍 کشور حساب بانکی آگهی دهنده را وارد کنید:",
            reply_markup=inline_cancel_keyboard(),
        )
    else:
        prompt = await update.message.reply_text("🌍 کشور حساب بانکی آگهی دهنده را وارد کنید:", reply_markup=inline_cancel_keyboard())
    context.user_data["last_prompt_message_id"] = prompt.message_id
    remember_cleanup_id(user_data_store, user_id, prompt.message_id, _EURO_CLEANUP_KEY)


async def handle_account_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.user_data.get("state") != UserState.EURO_ACCOUNT_COUNTRY.name:
        await update.message.reply_text(
            "⚠️ مرحلهٔ قبلی منقضی شده.\n"
            "از /menu منوی اصلی را بزنید و دوباره ثبت آگهی را شروع کنید."
        )
        return
    country = update.message.text.strip()
    if not country:
        return await update.message.reply_text("❌ لطفاً نام کشور را وارد کنید.", reply_markup=inline_cancel_keyboard())

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id not in user_data_store:
        user_data_store[user_id] = {"methods": [], "operation": ""}
    remember_cleanup_id(user_data_store, user_id, update.message.message_id, _EURO_CLEANUP_KEY)
    user_data_store[user_id]["account_country"] = country
    try:
        await update.message.delete()
    except Exception:
        pass
    await _ack_step(update, context, f"✅ کشور حساب بانکی آگهی دهنده: {country}")

    # Remove bot prompt message for a cleaner chat.
    try:
        mid = context.user_data.pop("last_prompt_message_id", None)
        if mid:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
    except Exception:
        pass

    # For BUY flow, skip "instant transfer" step (user receives money, not transfers).
    operation = (
        user_data_store.get(user_id, {}).get("operation")
        or context.user_data.get("operation")
        or ""
    ).strip()
    if operation == "خرید":
        context.user_data["state"] = UserState.EURO_AMOUNT.name
        return await ask_euro_amount(update, context)

    context.user_data["state"] = UserState.EURO_INSTANT_TRANSFER.name
    q = await context.bot.send_message(
        chat_id=chat_id,
        text="🏦 آیا امکانی واریز آنی را دارید:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("دارم", callback_data="instant_have"),
                InlineKeyboardButton("ندارم", callback_data="instant_dont_have"),
                InlineKeyboardButton("اطلاعی ندارم", callback_data="instant_unknown"),
            ],
            [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
        ]),
    )
    remember_cleanup_id(user_data_store, user_id, q.message_id, _EURO_CLEANUP_KEY)


async def handle_instant_transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.access_gate import ensure_registered_or_redirect

    if await ensure_registered_or_redirect(update, context):
        return
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass

    user_id = query.from_user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = {"methods": [], "operation": ""}

    value = None
    if query.data == "instant_have":
        value = "have"
    elif query.data == "instant_dont_have":
        value = "dont_have"
    elif query.data == "instant_unknown":
        value = "unknown"
    else:
        return

    user_data_store[user_id]["instant_transfer"] = value
    await _ack_step(update, context, f"✅ امکان واریز آنی: {_format_instant_transfer(value)}")
    # track the question message too (it will be deleted here, but safe)
    remember_cleanup_id(user_data_store, user_id, query.message.message_id if query.message else None, _EURO_CLEANUP_KEY)
    context.user_data["state"] = UserState.EURO_AMOUNT.name

    # Clean up the inline question message
    try:
        await query.message.delete()
    except Exception:
        pass

    return await ask_euro_amount(update, context)


async def ask_euro_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.user_data.get("state") != UserState.EURO_AMOUNT.name:
        await update.message.reply_text(
            "⚠️ مرحلهٔ قبلی منقضی شده.\n"
            "از /menu منوی اصلی را بزنید و دوباره ثبت آگهی را شروع کنید."
        )
        return
    msg = update.message
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})
    try:
        amount = int(msg.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError
        context.user_data['euro_amount'] = amount
        context.user_data['state'] = UserState.EURO_RATE.name
        remember_cleanup_id(user_data_store, user_id, msg.message_id, _EURO_CLEANUP_KEY)
    except:
        return await msg.reply_text("❌ لطفاً فقط عدد صحیح وارد کنید. مثال: 1200", reply_markup=inline_cancel_keyboard())

    # Delete previous bot prompt so only user input remains.
    try:
        mid = context.user_data.pop("last_prompt_message_id", None)
        if mid:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
    except Exception:
        pass

    await _ack_step(update, context, f"✅ مقدار یورو: {amount:,}")

    prompt = await msg.reply_text(
        "💰 لطفاً نرخ مورد نظر را به تومان بصورت کامل و بدون هیچ علامت و حرف اضافه ای وارد کنید\n(فقط عدد، مثال: 190000)",
        reply_markup=inline_cancel_keyboard()
    )
    context.user_data["last_prompt_message_id"] = prompt.message_id
    remember_cleanup_id(user_data_store, user_id, prompt.message_id, _EURO_CLEANUP_KEY)


async def ask_euro_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.user_data.get("state") != UserState.EURO_RATE.name:
        await update.message.reply_text(
            "⚠️ مرحلهٔ قبلی منقضی شده.\n"
            "از /menu منوی اصلی را بزنید و دوباره ثبت آگهی را شروع کنید."
        )
        return
    msg = update.message
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})
    try:
        rate = int(msg.text.strip().replace(",", ""))
        if rate <= 0:
            raise ValueError
        context.user_data['euro_rate'] = rate
        context.user_data['state'] = UserState.EURO_DESCRIPTION.name
        remember_cleanup_id(user_data_store, user_id, msg.message_id, _EURO_CLEANUP_KEY)
    except:
        return await msg.reply_text("❌ لطفاً فقط عدد صحیح وارد کنید. مثال: 98000", reply_markup=inline_cancel_keyboard())

    try:
        mid = context.user_data.pop("last_prompt_message_id", None)
        if mid:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
    except Exception:
        pass

    await _ack_step(update, context, f"✅ نرخ: {rate:,} تومان")

    prompt = await msg.reply_text(
        "📝 لطفاً توضیحات خود را وارد کنید (مثلاً ساعت یا شرایط انتقال):\nاگر توضیحی ندارید، بنویسید: ندارم",
        reply_markup=inline_cancel_keyboard()
    )
    context.user_data["last_prompt_message_id"] = prompt.message_id
    remember_cleanup_id(user_data_store, user_id, prompt.message_id, _EURO_CLEANUP_KEY)


async def preview_advert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != UserState.EURO_DESCRIPTION.name or not update.message:
        return

    msg = update.message
    desc_value = msg.text.strip()
    context.user_data['euro_description'] = desc_value
    context.user_data['state'] = UserState.EURO_CONFIRM_ADVERT.name
    user_id = update.effective_user.id
    user_data_store.setdefault(user_id, {})
    remember_cleanup_id(user_data_store, user_id, msg.message_id, _EURO_CLEANUP_KEY)
    await _ack_step(update, context, f"✅ توضیحات: {desc_value}")
    try:
        mid = context.user_data.pop("last_prompt_message_id", None)
        if mid:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
    except Exception:
        pass

    _, full_name = resolve_channel_advert_identity(context, user_id)
    amount = context.user_data['euro_amount']
    rate = context.user_data['euro_rate']
    desc = context.user_data['euro_description']
    operation = user_data_store.get(user_id, {}).get('operation', 'نامشخص')
    methods = user_data_store.get(user_id, {}).get('methods', [])
    methods_text = _format_methods_rtl(methods)
    account_country = user_data_store.get(user_id, {}).get("account_country")
    instant_transfer = _format_instant_transfer(user_data_store.get(user_id, {}).get("instant_transfer")) if operation != "خرید" else None

    advert_type = "خرید یورو" if operation == "خرید" else "فروش یورو"
    # Show chosen methods under "نوع آگهی" (buy=receive, sell=pay).
    methods_label = "روش‌های دریافت" if operation == "خرید" else "روش‌های پرداخت"
    methods_block = f"💳 <b>{methods_label}:</b>\n{methods_text}\n\n"

    preview = (
        "📣 <b>پیش‌نمایش آگهی</b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {advert_type}\n"
        f"{methods_block}"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"💰 <b>نرخ:</b> {rate:,} تومان\n"
        f"🧾 <b>کارمزد معامله:</b> {_format_fee_eur(amount)}\n\n"
        f"{_channel_country_html(account_country, operation=operation)}"
        f"{_format_optional_line('⚡ <b>امکان واریز آنی:</b>', instant_transfer)}"
        f"📄 <b>توضیحات:</b> {desc}"
        f"{format_channel_ad_footer(bot_username=(await context.bot.get_me()).username)}"
    )

    sent_preview = await msg.reply_text(
        preview,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید آگهی", callback_data="confirm_advert")],
            [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
        ])
    )
    remember_cleanup_id(user_data_store, user_id, sent_preview.message_id, _EURO_CLEANUP_KEY)



async def confirm_and_post_advert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config.settings import ADMIN_IDS
    from database.db import is_bot_enabled
    from handlers.access_gate import ensure_registered_or_redirect

    if await ensure_registered_or_redirect(update, context):
        return
    query = update.callback_query
    user_id = query.from_user.id
    admin_posting = bool(context.user_data.get("admin_post_advert_for"))
    if (
        not is_bot_enabled()
        and not admin_posting
        and user_id not in set(ADMIN_IDS or [])
    ):
        try:
            await query.answer("⛔️ ربات موقتاً غیرفعال است.", show_alert=True)
        except Exception:
            pass
        return
    await query.answer()
    chat_id = query.message.chat_id
    user_data_store.setdefault(user_id, {})
    owner_id, full_name = resolve_channel_advert_identity(context, user_id)
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        owner_id = int(user_id)

    member_ok, _ = await ensure_advert_channel_member(context.bot, owner_id)
    if not member_ok:
        kb = channel_membership_keyboard()
        member_err = channel_membership_required_html(at_confirm_step=True)
        try:
            await query.edit_message_text(
                member_err,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            context.user_data["channel_member_block_mid"] = query.message.message_id
        except Exception:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=member_err,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            context.user_data["channel_member_block_mid"] = sent.message_id
        return

    amount = context.user_data.get('euro_amount')
    rate = context.user_data.get('euro_rate')
    desc = context.user_data.get('euro_description')
    methods = user_data_store.get(user_id, {}).get('methods', [])
    operation = user_data_store.get(user_id, {}).get('operation', '---')
    methods_text = _format_methods_rtl(methods)
    account_country = user_data_store.get(user_id, {}).get("account_country")
    instant_transfer = _format_instant_transfer(user_data_store.get(user_id, {}).get("instant_transfer")) if operation != "خرید" else None

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO euro_adverts (
                user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                account_country, instant_transfer
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner_id, full_name, amount, rate, desc, ", ".join(methods), operation, account_country, instant_transfer),
        )
        advert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    bot_uname = (await context.bot.get_me()).username or ""

    # لینک موقت جایگزین‌شدنی
    placeholder_link = f"https://t.me/{CHANNEL_USERNAME}/..."

    methods_label_ch = "روش‌های دریافت" if operation == "خرید" else "روش‌های پرداخت"
    methods_block_ch = f"💳 <b>{methods_label_ch}:</b>\n{methods_text}\n\n"

    ad_text = (
        f"📋 <b><a href=\"{placeholder_link}\">آگهی شماره {advert_id}</a></b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {'خرید یورو' if operation == 'خرید' else 'فروش یورو'}\n"
        f"{methods_block_ch}"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"💰 <b>نرخ:</b> {rate:,} تومان\n"
        f"🧾 <b>کارمزد معامله:</b> {_format_fee_eur(amount)}\n\n"
        f"{_channel_country_html(account_country, operation=operation)}"
        f"{_format_optional_line('⚡ <b>امکان واریز آنی:</b>', instant_transfer)}"
        f"📄 <b>توضیحات:</b> {desc}"
        f"{format_channel_ad_footer(bot_username=bot_uname)}"
    )

    real_link = ""
    try:
        sent_msg = await context.bot.send_message(
            chat_id=ADVERT_CHANNEL_ID,
            text=ad_text,
            parse_mode=ParseMode.HTML,
            reply_markup=channel_ad_reply_markup(int(advert_id), bot_uname),
            disable_web_page_preview=True,
        )

        real_link = f"https://t.me/{CHANNEL_USERNAME}/{sent_msg.message_id}"
        updated_text = ad_text.replace(placeholder_link, real_link)

        await context.bot.edit_message_text(
            chat_id=ADVERT_CHANNEL_ID,
            message_id=sent_msg.message_id,
            text=updated_text,
            parse_mode=ParseMode.HTML,
            reply_markup=sent_msg.reply_markup,
            disable_web_page_preview=True,
        )

        with get_db() as conn:
            conn.execute(
                """
                UPDATE euro_adverts
                SET channel_chat_id = ?, channel_message_id = ?
                WHERE rowid = ?
                """,
                (str(ADVERT_CHANNEL_ID), int(sent_msg.message_id), int(advert_id)),
            )
    except Exception:
        try:
            with get_db() as conn:
                conn.execute(
                    "DELETE FROM euro_adverts WHERE rowid = ? AND user_id = ?",
                    (int(advert_id), int(owner_id)),
                )
        except Exception:
            pass
        ids = user_data_store.get(user_id, {}).pop(_EURO_CLEANUP_KEY, [])
        await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)
        try:
            await query.message.delete()
        except Exception:
            pass
        rm = admin_home_inline_keyboard() if admin_posting else main_menu_inline_keyboard
        fail_msg = (
            "❌ انتشار در کانال انجام نشد.\n"
            "ربات را در کانال <b>ادمین</b> کنید و مطمئن شوید <code>ADVERT_CHANNEL_ID</code> "
            "در تنظیمات درست است."
        )
        await send_or_replace_main_menu(
            context.bot,
            chat_id=chat_id,
            user_id=user_id,
            store=user_data_store,
            text=fail_msg,
            reply_markup=rm,
            parse_mode=ParseMode.HTML,
        )
        context.user_data.clear()
        context.user_data["state"] = (
            UserState.ADMIN_MENU.name if admin_posting else UserState.MAIN_MENU.name
        )
        return

    extra = [query.message.message_id] if query.message else []
    await cleanup_transient_dm_messages(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    await try_open_telegram_url(query, real_link)

    rm = admin_home_inline_keyboard() if admin_posting else main_menu_inline_keyboard
    menu_mid = await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text="✅ آگهی در کانال منتشر شد.",
        reply_markup=rm,
        parse_mode=ParseMode.HTML,
    )
    mark_flow_keep_message(user_data_store, user_id, context.user_data, menu_mid)

    context.user_data.clear()
    context.user_data["state"] = UserState.ADMIN_MENU.name if admin_posting else UserState.MAIN_MENU.name
