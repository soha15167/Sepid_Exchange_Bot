"""
main.py — Application entry / نقطهٔ ورود برنامه

EN:
  Builds the python-telegram-bot Application, runs DB migrations, registers all
  handlers (commands, callbacks, text routers). Group -1 runs access gates;
  group 0 routes euro/offer flows by UserState.

FA:
  ساخت Application، اجرای ensure_schema، ثبت هندلرها. گروه −۱ محدودیت و
  ثبت‌نام؛ روتر متن بر اساس state کاربر (آگهی، پیشنهاد، ادمین).
"""

from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from telegram import Update, BotCommand
import sys
from config.settings import BOT_TOKEN, ADMIN_IDS
from state import user_data_store
from utils.telegram_utils import (
    send_or_replace_main_menu,
    send_registration_welcome,
    cleanup_transient_dm_messages,
    is_main_offers_callback,
    is_my_offers_close_callback,
)
from handlers.services import (
    show_services_menu,
    handle_cancel,
    handle_service_operation_callback,
    handle_inline_cancel_callback,
    handle_services_menu_callback,
)
from handlers.registration import registration_handler
from handlers.callbacks import handle_payment_selection_callback
from handlers.euro_flow import (
    ask_euro_amount, ask_euro_rate, ask_euro_description,
    preview_advert, confirm_and_post_advert,
    handle_account_country,
    handle_instant_transfer_callback,
)
from handlers.exchange_flow import (
    start_exchange_flow, handle_exchange_choice,
    handle_exchange_instant_transfer_callback,
    handle_exchange_amount, handle_exchange_country_int, handle_exchange_city_int,
    handle_exchange_city_ir, handle_exchange_description,
    handle_confirm_exchange
)
from handlers.start_flow import handle_welcome, show_terms, handle_terms_response
from handlers.admin import (
    admin_entry,
    admin_router,
    admin_cancel_callback,
    admin_delete_advert_confirm_callback,
    admin_delete_user_confirm_callback,
    admin_advert_inline_callback,
    admin_exchange_edit_callback,
    admin_dashboard_callback,
    admin_neg_ad_command,
    _recover_admin_wizard_state,
)
from database.db import get_user
from database.db import ensure_schema
from keyboards.menus import (
    CHANNEL_RULES_REPLY_BUTTON_TEXT,
    FEE_INFO_REPLY_BUTTON_TEXT,
    MY_ADVERTS_REPLY_BUTTON_TEXT,
    MY_OFFERS_REPLY_BUTTON_TEXT,
    reply_menu_text_matches,
)
from models.enums import UserState
from handlers.offers import (
    handle_advert_owner_offer_action,
    handle_negotiation_message,
    handle_offer_advert_button,
    handle_offer_gate_agree,
    handle_offer_gate_custom,
    handle_offer_counter_amount_message,
    handle_offer_gate_back,
    handle_offer_rate_message,
    handle_offer_rate_cancel,
    handle_offer_description_message,
    handle_offer_account_country_message,
    handle_offer_preview_idle_message,
    handle_offer_final_confirm,
    handle_offer_final_cancel,
    handle_offer_desc_cancel,
    handle_offer_country_cancel,
    handle_offer_negotiate_stub,
    handle_neg_focus_callback,
    handle_neg_send_callback,
    handle_neg_gate_cancel_callback,
    handle_neg_prompt_cancel_callback,
    handle_offer_proposer_again,
    handle_offer_proposer_delete,
    handle_my_offers_callback,
    handle_my_offers_reply_message,
    handle_my_offers_close,
    handle_offer_proposer_edit_start,
    handle_offer_edit_rate_message,
    _pop_offer_draft_keys,
)
from handlers.channel_info import (
    handle_info_close_callback,
    handle_main_fees_callback,
    handle_main_fees_reply_message,
    handle_main_rules_callback,
    handle_main_rules_reply_message,
)
from handlers.error_handler import global_error_handler
from handlers.access_gate import restricted_user_gate, unregistered_user_gate
from handlers.user_adverts import (
    handle_main_my_adverts_callback,
    handle_main_my_adverts_message,
    handle_user_adv_callback,
    handle_user_own_advert_edit_message,
)


# بررسی وضعیت جاری و هدایت مرحله‌ای
def is_exchange_state(state):
    return state in [
        UserState.EXCHANGE_INIT.name,
        UserState.EXCHANGE_INSTANT_TRANSFER.name,
        UserState.EXCHANGE_AMOUNT.name,
        UserState.EXCHANGE_COUNTRY_INT.name,
        UserState.EXCHANGE_CITY_INT.name,
        UserState.EXCHANGE_CITY_IR.name,
        UserState.EXCHANGE_DESCRIPTION.name
    ]


async def euro_flow_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    EN: Routes TEXT messages by UserState (euro, exchange, offer, admin, negotiation).
    FA: هدایت پیام متنی بر اساس state کاربر.
    """
    u = update.effective_user
    if u and u.id in set(ADMIN_IDS or []):
        try:
            _recover_admin_wizard_state(u.id, context)
        except Exception:
            pass
    state = context.user_data.get("state")
    msg = update.message
    if msg and reply_menu_text_matches(MY_ADVERTS_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_my_adverts_message(update, context)
    if msg and reply_menu_text_matches("🚀 ثبت درخواست خدمات", msg.text or ""):
        return await show_services_menu(update, context)
    if msg and reply_menu_text_matches(MY_OFFERS_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_my_offers_reply_message(update, context)
    if msg and reply_menu_text_matches(CHANNEL_RULES_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_rules_reply_message(update, context)
    if msg and reply_menu_text_matches(FEE_INFO_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_fees_reply_message(update, context)
    if state == UserState.USER_EDIT_OWN_ADVERT.name:
        return await handle_user_own_advert_edit_message(update, context)

    if state == UserState.EURO_AMOUNT.name:
        return await ask_euro_rate(update, context)
    elif state == UserState.EURO_RATE.name:
        return await ask_euro_description(update, context)
    elif state == UserState.EURO_ACCOUNT_COUNTRY.name:
        return await handle_account_country(update, context)
    elif state == UserState.EURO_DESCRIPTION.name:
        return await preview_advert(update, context)
    elif is_exchange_state(state):
        if state == UserState.EXCHANGE_AMOUNT.name:
            return await handle_exchange_amount(update, context)
        elif state == UserState.EXCHANGE_COUNTRY_INT.name:
            return await handle_exchange_country_int(update, context)
        elif state == UserState.EXCHANGE_CITY_INT.name:
            return await handle_exchange_city_int(update, context)
        elif state == UserState.EXCHANGE_CITY_IR.name:
            return await handle_exchange_city_ir(update, context)
        elif state == UserState.EXCHANGE_DESCRIPTION.name:
            return await handle_exchange_description(update, context)
    elif state == UserState.NEGOTIATION.name:
        return await handle_negotiation_message(update, context)
    elif state == UserState.NEGOTIATION_GATE.name:
        if update.message and update.message.text:
            await update.message.reply_text(
                "\u200fلطفاً با دکمهٔ «ارسال پیام» یا «انصراف» ادامه دهید."
            )
        return
    elif context.user_data.get("offer_advert_id") is not None:
        offer_step = context.user_data.get("offer_flow_step")
        if offer_step == "counter_euro":
            return await handle_offer_counter_amount_message(update, context)
        if offer_step == "rate":
            return await handle_offer_rate_message(update, context)
        if offer_step == "account_country":
            return await handle_offer_account_country_message(update, context)
        if offer_step == "description":
            return await handle_offer_description_message(update, context)
        if offer_step == "preview":
            return await handle_offer_preview_idle_message(update, context)
    elif state == UserState.OFFER_ACCOUNT_COUNTRY.name:
        return await handle_offer_account_country_message(update, context)
    elif state == UserState.OFFER_COUNTER_EURO.name:
        return await handle_offer_counter_amount_message(update, context)
    elif state == UserState.OFFER_RATE.name:
        return await handle_offer_rate_message(update, context)
    elif state == UserState.OFFER_DESCRIPTION.name:
        return await handle_offer_description_message(update, context)
    elif state == UserState.OFFER_PREVIEW.name:
        return await handle_offer_preview_idle_message(update, context)
    elif state == UserState.OFFER_EDIT_RATE.name:
        return await handle_offer_edit_rate_message(update, context)


# مشاهده پروفایل
async def show_user_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db_user = get_user(tg_user.id)

    if not db_user:
        sent_nr = await update.effective_message.reply_text("❌ شما هنوز ثبت‌نام نکرده‌اید.")
        try:
            if update.message:
                await update.message.delete()
        except Exception:
            pass
        return sent_nr

    phone_raw = db_user.get('phone_number', '---')
    username_raw = f"@{tg_user.username}" if tg_user.username else 'ندارد'

    # Force LTR display so leading '+' and '@' stay at left.
    phone_display = f"\u200e{phone_raw}"
    username_display = f"\u200e{username_raw}"

    profile_text = f"""
👤 <b>مشخصات کاربر:</b>

🆔 <b>آیدی عددی:</b> {tg_user.id}
👨‍💼 <b>نام:</b> {db_user.get('full_name', '---')} {db_user.get('last_name', '')}
📧 <b>ایمیل:</b> {db_user.get('email', '---')}
🏠 <b>آدرس:</b> {db_user.get('address', '---')}
📱 <b>شماره:</b> <code>{phone_display}</code>
🧪 <b>یوزرنیم:</b> <code>{username_display}</code>
✅ <b>ثبت‌نام شده</b>
"""
    cid = update.effective_chat.id
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass

    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass

    await send_or_replace_main_menu(
        context.bot,
        chat_id=cid,
        user_id=tg_user.id,
        store=user_data_store,
        text=profile_text.strip(),
        parse_mode="HTML",
    )


# پیام شروع ربات
async def auto_start_notify(application: Application):
    bot_info = await application.bot.get_me()
    print(f"✅ ربات آماده‌ست: @{bot_info.username}")


async def show_main_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    cid = update.effective_chat.id
    if uid not in set(ADMIN_IDS or []) and get_user(uid) is None:
        context.user_data.clear()
        context.user_data["state"] = UserState.START.name
        await send_registration_welcome(
            context.bot, chat_id=cid, user_id=uid, store=user_data_store
        )
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    await cleanup_transient_dm_messages(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
        context_user_data=context.user_data,
    )
    _pop_offer_draft_keys(context)

    context.user_data.pop("neg_offer_id", None)
    context.user_data.pop("neg_offer_ids", None)
    context.user_data.pop("neg_gate_offer_id", None)
    gm = context.user_data.pop("neg_gate_mid", None)
    if gm:
        try:
            await context.bot.delete_message(chat_id=cid, message_id=int(gm))
        except Exception:
            pass
    context.user_data.pop("offer_edit_id", None)
    pm = context.user_data.pop("neg_prompt_mid", None)
    if pm:
        try:
            await context.bot.delete_message(chat_id=cid, message_id=int(pm))
        except Exception:
            pass
    context.user_data["state"] = UserState.MAIN_MENU.name
    await send_or_replace_main_menu(context.bot, chat_id=cid, user_id=uid, store=user_data_store)
    try:
        await update.message.delete()
    except Exception:
        pass


def main():
    """EN: Build app, register handlers, run polling. FA: راه‌اندازی و polling."""
    try:
        # Ensure Unicode logs/messages work on Windows consoles.
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("🚀 بوت رباط در حال اجراست...")
    # Ensure DB columns exist for newest features.
    try:
        ensure_schema()
    except Exception:
        pass
    application = Application.builder().token(BOT_TOKEN).build()

    # Group -1: access gates / گیت محدودیت و ثبت‌نام
    application.add_handler(MessageHandler(filters.ALL, restricted_user_gate), group=-1)
    application.add_handler(CallbackQueryHandler(restricted_user_gate), group=-1)
    application.add_handler(MessageHandler(filters.ALL, unregistered_user_gate), group=-1)
    application.add_handler(CallbackQueryHandler(unregistered_user_gate), group=-1)

    # Commands & registration / دستورات و ثبت‌نام
    application.add_handler(CommandHandler("start", handle_welcome))
    application.add_handler(CommandHandler("menu", show_main_menu_command))
    application.add_handler(CommandHandler("admin", admin_entry))
    application.add_handler(CommandHandler("neg_ad", admin_neg_ad_command))
    # Inline start/terms (prevents user-message spam)
    application.add_handler(CallbackQueryHandler(show_terms, pattern="^start_begin$"))
    # terms_accept باید قبل از سایر callbackها به ConversationHandler ثبت‌نام برسد
    application.add_handler(registration_handler)
    application.add_handler(CallbackQueryHandler(handle_terms_response, pattern="^terms_decline$"))

    # Main menu callbacks / منوی اصلی (اینلاین)
    application.add_handler(CallbackQueryHandler(show_user_profile, pattern="^main_profile$"))
    # Inline main menu entry
    application.add_handler(CallbackQueryHandler(show_services_menu, pattern="^main_services$"))
    application.add_handler(CallbackQueryHandler(handle_my_offers_callback, pattern=is_main_offers_callback))
    application.add_handler(CallbackQueryHandler(handle_main_my_adverts_callback, pattern=r"^main_my_adverts$"))
    application.add_handler(CallbackQueryHandler(handle_main_rules_callback, pattern=r"^main_rules$"))
    application.add_handler(CallbackQueryHandler(handle_main_fees_callback, pattern=r"^main_fees$"))
    application.add_handler(CallbackQueryHandler(handle_info_close_callback, pattern=r"^info_close$"))
    application.add_handler(CallbackQueryHandler(handle_user_adv_callback, pattern=r"^user_adv\|"))
    application.add_handler(CallbackQueryHandler(handle_services_menu_callback, pattern="^svc_"))

    # دکمه‌های انصراف و بازگشت
    application.add_handler(MessageHandler(
        filters.Regex(
            "^(?:❌ بازگشت|بازگشت ❌|❌ بازگشت به منوی اصلی|بازگشت به منوی اصلی ❌|❌ انصراف|انصراف ❌|انصراف|بازگشت|بازگشت به منوی اصلی|🏠 بازگشت به منو اصلی)$"
        ),
        handle_cancel
    ))
    # (Service selection is inline now)

    # ورود مرحله‌ای اطلاعات یورو / معاوضه
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, euro_flow_router), group=0)
    # Admin panel: run in later group to avoid hijacking normal flows
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_router), group=1)

    # تایید نهایی آگهی
    application.add_handler(CallbackQueryHandler(confirm_and_post_advert, pattern="^confirm_advert$"))
    application.add_handler(CallbackQueryHandler(handle_instant_transfer_callback, pattern="^instant_"))

    # معاوضه یورو به یورو
    application.add_handler(MessageHandler(filters.Regex("^💱 معاوضه Euro به Euro$"), start_exchange_flow))
    application.add_handler(CallbackQueryHandler(handle_exchange_instant_transfer_callback, pattern="^exchange_instant_"))
    application.add_handler(CallbackQueryHandler(handle_exchange_choice, pattern="^exchange_(can_transfer|no_transfer)$"))
    application.add_handler(CallbackQueryHandler(handle_confirm_exchange, pattern="^confirm_exchange$"))
    application.add_handler(CallbackQueryHandler(handle_service_operation_callback, pattern="^service_op_"))
    application.add_handler(CallbackQueryHandler(handle_inline_cancel_callback, pattern="^inline_cancel$"))
    application.add_handler(CallbackQueryHandler(admin_dashboard_callback, pattern=r"^adm\|"))
    application.add_handler(CallbackQueryHandler(admin_cancel_callback, pattern="^admin_cancel$"))
    application.add_handler(CallbackQueryHandler(admin_delete_advert_confirm_callback, pattern="^admin_del_adv_yes_"))
    application.add_handler(CallbackQueryHandler(admin_delete_user_confirm_callback, pattern="^admin_del_user_yes_"))
    application.add_handler(CallbackQueryHandler(admin_exchange_edit_callback, pattern=r"^admin_xd\|"))
    application.add_handler(CallbackQueryHandler(admin_advert_inline_callback, pattern="^admin_adv_"))

    # پیشنهاد به آگهی (دکمه زیر پست کانال)
    application.add_handler(CallbackQueryHandler(handle_offer_advert_button, pattern=r"^offer_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_gate_agree, pattern=r"^offer_gate_agree\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_gate_custom, pattern=r"^offer_gate_custom\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_gate_back, pattern=r"^offer_gate_back\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_rate_cancel, pattern=r"^offer_rate_cancel\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_desc_cancel, pattern=r"^offer_desc_cancel\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_country_cancel, pattern=r"^offer_country_cancel\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_final_confirm, pattern=r"^offer_final_confirm$"))
    application.add_handler(CallbackQueryHandler(handle_offer_final_cancel, pattern=r"^offer_final_cancel$"))
    application.add_handler(CallbackQueryHandler(handle_neg_send_callback, pattern=r"^neg_send\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_neg_gate_cancel_callback, pattern=r"^neg_gc\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_neg_prompt_cancel_callback, pattern=r"^neg_pc\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_neg_focus_callback, pattern=r"^neg_focus\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_advert_owner_offer_action, pattern=r"^adv_o\|"))
    application.add_handler(CallbackQueryHandler(handle_offer_proposer_edit_start, pattern=r"^offer_edit\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_my_offers_close, pattern=is_my_offers_close_callback))
    application.add_handler(CallbackQueryHandler(handle_offer_proposer_delete, pattern=r"^offer_del\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_proposer_again, pattern=r"^offer_again\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_offer_negotiate_stub, pattern=r"^offer_neg\|\d+$"))

    # انتخاب روش پرداخت (فقط callbackهای فلو یورو؛ بدون pattern همهٔ کلیک‌ها را answer می‌کرد)
    application.add_handler(
        CallbackQueryHandler(
            handle_payment_selection_callback,
            pattern=r"^(cancel|confirm|confirm_methods|method_iban|method_paypal|method_wise|method_revolut|method_exchange)$",
        )
    )

    # دیباگ (اختیاری)
    # مدیریت خطا
    application.add_error_handler(global_error_handler)

    # بعد از شروع
    async def post_init(app):
        await auto_start_notify(app)
        try:
            await app.bot.set_my_commands(
                [
                    BotCommand("start", "شروع و ثبت‌نام"),
                    BotCommand("menu", "نمایش منوی اصلی"),
                    BotCommand("admin", "پنل مدیریت"),
                ]
            )
        except Exception:
            pass

    application.post_init = post_init
    application.run_polling()


if __name__ == "__main__":
    main()
