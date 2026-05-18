"""
handlers/exchange_flow.py — Euro-to-Euro exchange / معاوضه یورو به یورو

EN: Delivery method, amount, countries/cities, description → channel (rate 0).
FA: روش تحویل، مقدار، شهرها، توضیحات → آگهی معاوضه در کانال.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from state import user_data_store
from models.enums import UserState
from keyboards.admin_home import admin_home_inline_keyboard
from keyboards.menus import (
    inline_cancel_keyboard,
    main_menu_inline_keyboard,
)
from handlers.euro_flow import resolve_channel_advert_identity
from config.settings import ADVERT_CHANNEL_ID, CHANNEL_USERNAME
from database.db import get_db
from utils.telegram_utils import remember_cleanup_id, cleanup_ids, send_or_replace_main_menu
from utils.euro_fees import format_fee_eur as _format_fee_eur
from handlers.offers import offer_proposal_inline_button


_EXCHANGE_CLEANUP_KEY = "exchange_cleanup_message_ids"


def _remember_cleanup(user_id: int, message_id: int | None) -> None:
    remember_cleanup_id(user_data_store, user_id, message_id, _EXCHANGE_CLEANUP_KEY)


async def _cleanup_exchange_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    ids: list[int] = user_data_store.get(user_id, {}).pop(_EXCHANGE_CLEANUP_KEY, [])
    await cleanup_ids(context.bot, chat_id=chat_id, ids=ids)


_RTL = "\u200f"


def _exch_country_line(raw) -> str:
    c = (raw or "").strip()
    if not c or c in ("-", "—", "–"):
        return ""
    return f"🗺️ <b>کشور (خارج از ایران):</b> {c}\n"


def _get_exchange_side(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    # Prefer persisted side from user_data_store; fallback to context if present.
    side = user_data_store.get(user_id, {}).get("exchange_side")
    if side:
        return side
    return context.user_data.get("exchange_side", "")


async def start_exchange_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    preserved_posting = context.user_data.get("admin_post_advert_for")
    # Preserve cleanup ids across restarts so "انصراف" can delete everything.
    bucket = user_data_store.setdefault(user_id, {})
    existing_cleanup = bucket.get(_EXCHANGE_CLEANUP_KEY, []).copy()
    exchange_side = bucket.get("exchange_side")
    bucket.clear()
    if existing_cleanup:
        bucket[_EXCHANGE_CLEANUP_KEY] = existing_cleanup
    if exchange_side:
        bucket["exchange_side"] = exchange_side
    context.user_data.clear()
    if preserved_posting:
        context.user_data["admin_post_advert_for"] = preserved_posting
    context.user_data['state'] = UserState.EXCHANGE_INIT.name
    context.user_data['operation'] = 'معاوضه'
    side = _get_exchange_side(user_id, context)
    context.user_data["exchange_side"] = side
    if side == "خرید":
        message_text = "📥 لطفاً روش دریافت یورو را انتخاب کنید:"
        can_transfer_label = "امکان دریافت به حساب دارم"
        no_transfer_label = "امکان دریافت حضوری دارم (دریافت حضوری)"
    else:
        message_text = "📤 لطفاً روش تحویل یورو را انتخاب کنید:"
        can_transfer_label = "امکان واریز دارم"
        no_transfer_label = "امکان واریز ندارم (تحویل حضوری)"
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(can_transfer_label, callback_data="exchange_can_transfer")],
        [InlineKeyboardButton(no_transfer_label, callback_data="exchange_no_transfer")],
        [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
    ])

    if query:
        try:
            await query.answer()
        except Exception:
            pass
        # The callback's message might have been deleted earlier (e.g. for chat cleanup).
        # Fall back to sending a new message in that case.
        try:
            await query.edit_message_text(message_text, reply_markup=reply_markup)
            _remember_cleanup(user_id, query.message.message_id if query.message else None)
        except Exception:
            sent = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_text,
                reply_markup=reply_markup,
            )
            _remember_cleanup(user_id, sent.message_id)
    else:
        sent = await update.message.reply_text(message_text, reply_markup=reply_markup)
        _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    user_id = query.from_user.id
    side = _get_exchange_side(user_id, context)

    # Remove the inline keyboard message after selection.
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    if choice == "exchange_can_transfer":
        if side == "خرید":
            context.user_data['exchange_method'] = "امکان دریافت به حساب دارم"
            context.user_data['state'] = UserState.EXCHANGE_AMOUNT.name
        else:
            context.user_data['exchange_method'] = "امکان واریز به حساب دارم"
            context.user_data['state'] = UserState.EXCHANGE_INSTANT_TRANSFER.name
        # ensure storage exists
        if user_id not in user_data_store:
            user_data_store[user_id] = {}
        # Show the selected option in chat (will be cleaned up later).
        label = "روش دریافت" if side == "خرید" else "روش تحویل"
        selected_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ {label}: {context.user_data['exchange_method']}",
        )
        _remember_cleanup(user_id, selected_msg.message_id)
        # BUY path: skip instant-transfer question.
        if side == "خرید":
            sent = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="💶 لطفاً مقدار یورو را وارد کنید:",
                reply_markup=inline_cancel_keyboard(),
            )
            _remember_cleanup(user_id, sent.message_id)
            return
        sent = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🏦 آیا امکان واریز آنی را دارید:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("ندارم", callback_data="exchange_instant_dont_have"),
                    InlineKeyboardButton("دارم", callback_data="exchange_instant_have"),
                    InlineKeyboardButton("اطلاعی ندارم", callback_data="exchange_instant_unknown"),
                ],
                [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
            ]),
        )
        _remember_cleanup(user_id, sent.message_id)
        return
    elif choice == "exchange_no_transfer":
        context.user_data['exchange_method'] = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
        context.user_data.pop("exchange_instant_transfer", None)
        if user_id in user_data_store:
            user_data_store[user_id].pop("exchange_instant_transfer", None)
        selected_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ {'روش دریافت' if side == 'خرید' else 'روش تحویل'}: {context.user_data['exchange_method']}",
        )
        _remember_cleanup(user_id, selected_msg.message_id)

    context.user_data['state'] = UserState.EXCHANGE_AMOUNT.name

    sent = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="💶 لطفاً مقدار یورو را وارد کنید:",
        reply_markup=inline_cancel_keyboard()
    )
    _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_instant_transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    if data == "exchange_instant_have":
        value = "دارم"
    elif data == "exchange_instant_dont_have":
        value = "ندارم"
    elif data == "exchange_instant_unknown":
        value = "اطلاعی ندارم"
    else:
        return
    context.user_data["exchange_instant_transfer"] = value
    user_id = query.from_user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = {}
    user_data_store[user_id]["exchange_instant_transfer"] = value

    selected_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ امکان واریز آنی: {value}",
    )
    _remember_cleanup(user_id, selected_msg.message_id)

    # Remove the inline question message after selection.
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    context.user_data['state'] = UserState.EXCHANGE_AMOUNT.name

    sent = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="💶 لطفاً مقدار یورو را وارد کنید:",
        reply_markup=inline_cancel_keyboard(),
    )
    _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError
        context.user_data['exchange_amount'] = amount
        _remember_cleanup(user_id, update.message.message_id)
        ack = await update.message.reply_text(f"✅ مقدار یورو: {amount:,}")
        _remember_cleanup(user_id, ack.message_id)

        context.user_data['state'] = UserState.EXCHANGE_COUNTRY_INT.name
        sent = await update.message.reply_text(
            "🌍 لطفاً کشور حساب بانکی آگهی دهنده (خارج از ایران) را وارد کنید:",
            reply_markup=inline_cancel_keyboard(),
        )
        _remember_cleanup(user_id, sent.message_id)

    except (TypeError, ValueError):
        sent = await update.message.reply_text("❌ لطفاً عدد صحیح وارد کنید.", reply_markup=inline_cancel_keyboard())
        _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_country_int(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['exchange_country_int'] = update.message.text.strip()
    user_id = update.effective_user.id
    _remember_cleanup(user_id, update.message.message_id)
    ack = await update.message.reply_text(
        f"✅ کشور حساب بانکی آگهی دهنده: {context.user_data['exchange_country_int']}"
    )
    _remember_cleanup(user_id, ack.message_id)

    # Ask for "city abroad" only if in-person (no transfer).
    side = _get_exchange_side(user_id, context)
    in_person_value = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
    if context.user_data.get("exchange_method") == in_person_value:
        context.user_data['state'] = UserState.EXCHANGE_CITY_INT.name
        sent = await update.message.reply_text(
            "🌍 لطفا نام شهر خارج از ایران را وارد کنید:",
            reply_markup=inline_cancel_keyboard()
        )
        _remember_cleanup(user_id, sent.message_id)
        return

    # If they can transfer, skip foreign city and go straight to Iran city.
    context.user_data['exchange_city_int'] = "—"
    context.user_data['state'] = UserState.EXCHANGE_CITY_IR.name
    sent = await update.message.reply_text(
        "🏙️ لطفا نام شهر داخل ایران را وارد کنید:",
        reply_markup=inline_cancel_keyboard()
    )
    _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_city_int(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['exchange_city_int'] = update.message.text.strip()
    context.user_data['state'] = UserState.EXCHANGE_CITY_IR.name
    user_id = update.effective_user.id
    _remember_cleanup(user_id, update.message.message_id)
    ack = await update.message.reply_text(f"✅ شهر خارج از ایران: {context.user_data['exchange_city_int']}")
    _remember_cleanup(user_id, ack.message_id)

    sent = await update.message.reply_text(
        "🏙️ لطفا نام شهر داخل ایران را وارد کنید:",
        reply_markup=inline_cancel_keyboard()
    )
    _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_city_ir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['exchange_city_ir'] = update.message.text.strip()
    context.user_data['state'] = UserState.EXCHANGE_DESCRIPTION.name
    user_id = update.effective_user.id
    _remember_cleanup(user_id, update.message.message_id)
    ack = await update.message.reply_text(f"✅ شهر ایران: {context.user_data['exchange_city_ir']}")
    _remember_cleanup(user_id, ack.message_id)

    sent = await update.message.reply_text(
        "📝 لطفا توضیحات خود را وارد کنید. اگر توضیحی ندارید، بنویسید: ندارم",
        reply_markup=inline_cancel_keyboard()
    )
    _remember_cleanup(user_id, sent.message_id)


async def handle_exchange_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['exchange_description'] = update.message.text.strip()
    context.user_data['state'] = UserState.EXCHANGE_CONFIRM.name
    user_id = update.effective_user.id
    _remember_cleanup(user_id, update.message.message_id)
    ack = await update.message.reply_text(f"✅ توضیحات: {context.user_data['exchange_description']}")
    _remember_cleanup(user_id, ack.message_id)

    user_id = update.effective_user.id
    _, full_name = resolve_channel_advert_identity(context, user_id)
    side = _get_exchange_side(user_id, context)
    method = context.user_data.get("exchange_method", "-")
    instant = context.user_data.get("exchange_instant_transfer") or user_data_store.get(user_id, {}).get("exchange_instant_transfer")
    amount = context.user_data.get("exchange_amount", "-")
    city_ir = context.user_data.get("exchange_city_ir", "-")
    country_int = context.user_data.get("exchange_country_int", "-")
    city_int = context.user_data.get("exchange_city_int", "-")
    desc = context.user_data.get("exchange_description", "-")

    # Force RTL for the whole Iran-city line (better alignment with 🇮🇷).
    rtl_city_ir_line = f"\u200f🏙️ <b>شهر ایران:</b> {city_ir}"
    show_instant = side != "خرید" and context.user_data.get("exchange_method") == "امکان واریز به حساب دارم"
    instant_value = instant or "—"
    instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant_value}\n" if show_instant else ""
    in_person_value = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
    show_foreign_city = context.user_data.get("exchange_method") == in_person_value
    foreign_city_line = f"🌆 <b>شهر خارج:</b> {city_int}\n" if show_foreign_city else ""
    foreign_country_line = _exch_country_line(country_int)
    preview = (
        "📣 <b>پیش‌نمایش آگهی معاوضه</b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {'خرید یورو' if side == 'خرید' else 'فروش یورو'}\n"
        "🔀 <b>روش معاوضه:</b>\n"
        f"{_RTL}یورو به یورو\n\n"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"🧾 <b>کارمزد (هر طرف):</b> {_format_fee_eur(amount if isinstance(amount, int) else None)}\n\n"
        f"{foreign_country_line}"
        f"{foreign_city_line}"
        f"{rtl_city_ir_line}\n\n"
        f"📦 <b>{'روش دریافت' if side == 'خرید' else 'روش تحویل'}:</b> {method}\n"
        f"{instant_line}\n"
        f"📄 <b>توضیحات:</b> {desc}\n\n"
        f"🤖 <b>ربات:</b> @{(await context.bot.get_me()).username}\n"
    )

    sent = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=preview,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید آگهی", callback_data="confirm_exchange")],
            [InlineKeyboardButton("❌ انصراف", callback_data="inline_cancel")],
        ])
    )
    _remember_cleanup(user_id, sent.message_id)



async def handle_confirm_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    admin_posting = bool(context.user_data.get("admin_post_advert_for"))
    owner_id, full_name = resolve_channel_advert_identity(context, user_id)
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        owner_id = int(user_id)

    side = _get_exchange_side(user_id, context)
    method = context.user_data.get("exchange_method", "-")
    instant = context.user_data.get("exchange_instant_transfer") or user_data_store.get(user_id, {}).get("exchange_instant_transfer")
    amount = context.user_data.get("exchange_amount", "-")
    city_ir = context.user_data.get("exchange_city_ir", "-")
    country_int = context.user_data.get("exchange_country_int", "-")
    city_int = context.user_data.get("exchange_city_int", "-")
    desc = context.user_data.get("exchange_description", "-")
    show_instant = side != "خرید" and context.user_data.get("exchange_method") == "امکان واریز به حساب دارم"

    # cleanup chat messages (best effort)
    await _cleanup_exchange_chat(context, chat_id=query.message.chat_id, user_id=user_id)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO euro_adverts (
                user_id, full_name, euro_amount, rate_toman, description, methods, operation, city_ir, city_int,
                account_country, instant_transfer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner_id, full_name, amount, 0, desc, method, "معاوضه", city_ir, city_int, country_int, instant if show_instant else None),
        )
        advert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    bot_uname = (await context.bot.get_me()).username or ""

    # لینک موقت برای جایگزینی
    placeholder_link = f"https://t.me/{CHANNEL_USERNAME}/..."

    rtl_city_ir_line = f"\u200f🏙️ <b>شهر ایران:</b> {city_ir}"
    instant_value = instant or "—"
    instant_line = f"⚡ <b>امکان واریز آنی:</b> {instant_value}\n" if show_instant else ""
    in_person_value = "دریافت حضوری" if side == "خرید" else "تحویل حضوری"
    show_foreign_city = context.user_data.get("exchange_method") == in_person_value
    foreign_city_line = f"🌆 <b>شهر خارج:</b> {city_int}\n" if show_foreign_city else ""
    foreign_country_line = _exch_country_line(country_int)
    ad_text = (
        f"📋 <b><a href=\"{placeholder_link}\">آگهی شماره {advert_id}</a></b>\n\n"
        f"👤 <b>آگهی‌دهنده:</b> {full_name}\n"
        f"🏷️ <b>نوع آگهی:</b> {'خرید یورو' if side == 'خرید' else 'فروش یورو'}\n"
        "🔀 <b>روش معاوضه:</b>\n"
        f"{_RTL}یورو به یورو\n\n"
        f"💶 <b>مقدار:</b> {amount:,} یورو\n"
        f"🧾 <b>کارمزد (هر طرف):</b> {_format_fee_eur(amount if isinstance(amount, int) else None)}\n\n"
        f"{foreign_country_line}"
        f"{foreign_city_line}"
        f"{rtl_city_ir_line}\n\n"
        f"📦 <b>{'روش دریافت' if side == 'خرید' else 'روش تحویل'}:</b> {method}\n"
        f"{instant_line}\n"
        f"📄 <b>توضیحات:</b> {desc}\n\n"
        f"🤖 <b>ربات:</b> @{bot_uname}\n"
    )

    chat_id = query.message.chat_id
    real_link = ""
    try:
        sent_msg = await context.bot.send_message(
            chat_id=ADVERT_CHANNEL_ID,
            text=ad_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[offer_proposal_inline_button(int(advert_id), bot_uname)]]
            ),
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

    try:
        await query.message.delete()
    except Exception:
        pass
    rm = admin_home_inline_keyboard() if admin_posting else main_menu_inline_keyboard
    ok_msg = (
        "✅ آگهی با موفقیت در کانال منتشر شد.\n"
        f'🔗 <a href="{real_link}">مشاهدهٔ پست در کانال</a>'
    )
    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        text=ok_msg,
        reply_markup=rm,
        parse_mode=ParseMode.HTML,
    )

    context.user_data.clear()
    context.user_data["state"] = UserState.ADMIN_MENU.name if admin_posting else UserState.MAIN_MENU.name
