"""
main.py — Application entry | نقطهٔ ورود برنامه

EN:
  Builds the python-telegram-bot Application, runs ensure_schema(), registers handlers.
  Group -1: access/registration gates. Group 0: deal_gate (receipts/accounts, priority).
  Groups 1+: euro/offer wizards, admin. Deal admin callbacks: adm|pay|, tomset, eurcfm, stom.

FA:
  ساخت Application، migration، ثبت هندلرها. گروه −۱: ثبت‌نام و محدودیت.
  گروه ۰: deal_gate (فیش و حساب معامله). callbackهای ادمین معامله: pay, tomset, eurcfm, stom.

Docs: README.md, docs/DEAL_GATE.md, docs/CODE_OVERVIEW.md (EN + FA).
"""

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    JobQueue,
    filters,
    ContextTypes,
    ApplicationHandlerStop,
)
from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.constants import ChatType
import logging
import sys
from datetime import time
from zoneinfo import ZoneInfo

from config.settings import (
    ADMIN_IDS,
    BONBAST_DAILY_HOUR,
    BONBAST_DAILY_MINUTE,
    BONBAST_DAILY_POST_ENABLED,
    BOT_TOKEN,
)
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
from handlers.channel_gate import handle_channel_member_ack
from handlers.admin import (
    admin_entry,
    admin_router,
    admin_add_user_otp_callback,
    admin_cancel_callback,
    admin_delete_advert_confirm_callback,
    admin_delete_user_confirm_callback,
    admin_advert_inline_callback,
    admin_exchange_edit_callback,
    admin_dashboard_callback,
    admin_neg_ad_command,
)
from handlers.bank_cards import admin_cards_command, bank_cards_callback
from handlers.iran_panel_sync import (
    iran_panel_fill_router,
    iran_panel_sync_router,
    iran_panel_tx_callback,
    txin_command,
    txout_command,
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
    handle_offer_back_euro_amount,
    handle_offer_description_message,
    handle_offer_account_country_message,
    handle_offer_preview_idle_message,
    route_offer_flow_message,
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
    handle_my_offers_page_callback,
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
from handlers.deal_gate import (
    admin_deal_gate_account_photo_router,
    deal_admin_euro_settled_callback,
    deal_admin_party_proxy_callback,
    deal_admin_payment_callback,
    deal_admin_seller_toman_receipt_callback,
    deal_admin_send_buyer_eur_account_callback,
    deal_admin_toman_settled_callback,
    deal_admin_view_outbound_logs_callback,
    deal_gate_accounts_photo_router,
    deal_gate_accounts_router,
    deal_gate_callback,
    deal_gate_group0_photo_router,
    deal_gate_group0_text_router,
)
from handlers.error_handler import global_error_handler
from handlers.access_gate import (
    bot_disabled_gate,
    ensure_registered_or_redirect,
    restricted_user_gate,
    unregistered_user_gate,
)
from handlers.user_adverts import (
    handle_main_my_adverts_callback,
    handle_main_my_adverts_message,
    handle_user_adv_callback,
    handle_user_adv_find_message,
    handle_user_own_advert_edit_message,
)
from handlers.misc_callbacks import handle_bot_closed_callback, handle_noop_callback


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


async def _dispatch_user_flow_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Route advert / offer / exchange text. Returns True if handled."""
    if not update.message or context.user_data is None:
        return False
    state = context.user_data.get("state")

    if state == UserState.EURO_AMOUNT.name:
        await ask_euro_rate(update, context)
        return True
    if state == UserState.EURO_RATE.name:
        await ask_euro_description(update, context)
        return True
    if state == UserState.EURO_ACCOUNT_COUNTRY.name:
        await handle_account_country(update, context)
        return True
    if state == UserState.EURO_DESCRIPTION.name:
        await preview_advert(update, context)
        return True
    if state == UserState.EURO_INSTANT_TRANSFER.name:
        await update.message.reply_text(
            "\u200fلطفاً با دکمه‌های «دارم / ندارم / اطلاعی ندارم» در پیام بالا ادامه دهید."
        )
        return True
    if state == UserState.SERVICE_SELECTION.name:
        await update.message.reply_text(
            "\u200fلطفاً روش پرداخت را با دکمه‌های بالا انتخاب کنید."
        )
        return True
    if state == UserState.EURO_CONFIRM_ADVERT.name:
        await update.message.reply_text(
            "\u200fلطفاً با دکمه «تایید آگهی» یا «انصراف» ادامه دهید."
        )
        return True

    if is_exchange_state(state):
        if state == UserState.EXCHANGE_AMOUNT.name:
            await handle_exchange_amount(update, context)
            return True
        if state == UserState.EXCHANGE_COUNTRY_INT.name:
            await handle_exchange_country_int(update, context)
            return True
        if state == UserState.EXCHANGE_CITY_INT.name:
            await handle_exchange_city_int(update, context)
            return True
        if state == UserState.EXCHANGE_CITY_IR.name:
            await handle_exchange_city_ir(update, context)
            return True
        if state == UserState.EXCHANGE_DESCRIPTION.name:
            await handle_exchange_description(update, context)
            return True
        if state in (
            UserState.EXCHANGE_INIT.name,
            UserState.EXCHANGE_INSTANT_TRANSFER.name,
        ):
            await update.message.reply_text(
                "\u200fلطفاً با دکمه‌های پیام قبلی ادامه دهید."
            )
            return True

    if await route_offer_flow_message(update, context):
        return True
    if state == UserState.OFFER_EDIT_RATE.name:
        await handle_offer_edit_rate_message(update, context)
        return True
    return False


async def wizard_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    ثبت آگهی / پیشنهاد / معاوضه — group 1؛ deal_gate در group 0 جدا اجرا می‌شود.
    """
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    if not update.message or context.user_data is None:
        return
    if await ensure_registered_or_redirect(update, context):
        raise ApplicationHandlerStop

    from utils.flow_guards import user_advert_offer_wizard_active

    flow_active = user_advert_offer_wizard_active(context)
    if not flow_active:
        return

    handled = await _dispatch_user_flow_text(update, context)
    if handled:
        logger.info(
            "wizard_text: uid=%s state=%r handled",
            update.effective_user.id if update.effective_user else None,
            context.user_data.get("state"),
        )
    elif flow_active:
        logger.warning(
            "wizard_text: unhandled uid=%s state=%r offer_step=%r text=%r",
            update.effective_user.id if update.effective_user else None,
            context.user_data.get("state"),
            (context.user_data.get("offer_flow_step") or "").strip(),
            (update.message.text or "")[:60],
        )
        await update.message.reply_text(
            "\u200f⚠️ مرحلهٔ فلو شناخته نشد — /menu بزنید و دوباره شروع کنید."
        )
    raise ApplicationHandlerStop


async def euro_flow_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    EN: Routes TEXT messages by UserState (euro, exchange, offer, admin, negotiation).
    FA: هدایت پیام متنی بر اساس state کاربر.
    """
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    if context.user_data is None:
        return
    if await ensure_registered_or_redirect(update, context):
        return
    u = update.effective_user
    state = context.user_data.get("state")
    msg = update.message
    if msg and reply_menu_text_matches(MY_ADVERTS_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_my_adverts_message(update, context)
    if msg and reply_menu_text_matches("🚀 درخواست خدمات", msg.text or ""):
        return await show_services_menu(update, context)
    if msg and reply_menu_text_matches(MY_OFFERS_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_my_offers_reply_message(update, context)
    if msg and reply_menu_text_matches(CHANNEL_RULES_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_rules_reply_message(update, context)
    if msg and reply_menu_text_matches(FEE_INFO_REPLY_BUTTON_TEXT, msg.text or ""):
        return await handle_main_fees_reply_message(update, context)
    if context.user_data.get("user_adv_find_prompt"):
        return await handle_user_adv_find_message(update, context)
    if state == UserState.USER_EDIT_OWN_ADVERT.name:
        return await handle_user_own_advert_edit_message(update, context)

    from database.db import deal_gate_active_for_user
    from utils.flow_guards import user_flow_text_active

    if u and deal_gate_active_for_user(u.id) and not user_flow_text_active(context):
        return

    # ثبت آگهی/پیشنهاد/معاوضه در group=1 (wizard_text_router) هندل می‌شود؛
    # تکرار _dispatch_user_flow_text اینجا باعث پیام اشتباه «تایید آگهی» بعد از پیش‌نمایش می‌شد.

    if state == UserState.NEGOTIATION.name:
        return await handle_negotiation_message(update, context)
    elif state == UserState.NEGOTIATION_GATE.name:
        if update.message and update.message.text:
            await update.message.reply_text(
                "\u200fلطفاً با دکمهٔ «ارسال پیام» یا «انصراف» ادامه دهید."
            )
        return

    if not msg or not msg.text:
        return
    from handlers.admin import _admin_should_skip_wizard_recovery

    uid = u.id if u else None
    if _admin_should_skip_wizard_recovery(context):
        offer_step = (context.user_data.get("offer_flow_step") or "").strip()
        logger.warning(
            "flow_route: unhandled uid=%s state=%r offer_step=%r text=%r",
            uid,
            state,
            offer_step,
            (msg.text or "")[:60],
        )
        extra = f"\n(state={state or '—'})" if uid in set(ADMIN_IDS or []) else ""
        await msg.reply_text(
            f"\u200f⚠️ مرحلهٔ فلو شناخته نشد — /menu بزنید و دوباره شروع کنید.{extra}"
        )


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
    display_name = (db_user.get("display_name") or "").strip() or "—"

    profile_text = f"""
👤 <b>مشخصات کاربر:</b>

🆔 <b>آیدی عددی:</b> {tg_user.id}
👨‍💼 <b>نام:</b> {db_user.get('full_name', '---')} {db_user.get('last_name', '')}
🏷️ <b>نام نمایشی در آگهی:</b> {display_name}
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


logger = logging.getLogger(__name__)


def _create_application() -> Application:
    """
    EN: Build Application with explicit JobQueue (systemd sometimes misses PTB extras).
    FA: ساخت Application با JobQueue صریح — در برخی سرورها job-queue خودکار فعال نمی‌شود.
    """
    builder = Application.builder().token(BOT_TOKEN)
    try:
        builder = builder.job_queue(JobQueue())
    except Exception as exc:
        logger.warning("JobQueue not attached at startup: %s", exc)
    app = builder.build()
    if app.job_queue:
        logger.info("JobQueue ready (python=%s)", sys.executable)
    else:
        logger.warning(
            "JobQueue unavailable — run: python3 -m pip install "
            "'python-telegram-bot[job-queue]==20.7'"
        )
    return app


async def auto_start_notify(application: Application):
    from config.settings import ADMIN_IDS, ADMIN_NOTIFY_CHAT_IDS

    bot_info = await application.bot.get_me()
    print(f"✅ ربات آماده‌ست: @{bot_info.username}")
    print(f"✅ deal-admin notify: users={ADMIN_IDS} extra_chats={ADMIN_NOTIFY_CHAT_IDS or '—'}")
    try:
        from handlers.offers import OFFER_RATE_REJECTION_BUILD

        print(f"✅ offer rate rejection build: {OFFER_RATE_REJECTION_BUILD}")
    except Exception:
        pass


def _setup_seller_stom_reminder_job(application: Application) -> None:
    from handlers.deal_gate import run_seller_stom_reminder_sweep

    if not application.job_queue:
        return

    async def _sweep(context):
        n = await run_seller_stom_reminder_sweep(context.bot)
        if n:
            logger.info("seller_stom_reminder_sweep: sent %s", n)

    application.job_queue.run_repeating(
        _sweep,
        interval=3600,
        first=600,
        name="seller_stom_reminder_sweep",
    )
    logger.info("Seller stom close reminder sweep every 1h (8h interval)")


def _setup_daily_admin_report_job(application: Application) -> None:
    from config.settings import DAILY_REPORT_ENABLED, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE
    from handlers.daily_report import post_daily_admin_report

    if not DAILY_REPORT_ENABLED:
        return
    if not application.job_queue:
        return
    when = time(
        hour=DAILY_REPORT_HOUR,
        minute=DAILY_REPORT_MINUTE,
        tzinfo=ZoneInfo("Asia/Tehran"),
    )
    application.job_queue.run_daily(
        post_daily_admin_report,
        time=when,
        name="daily_admin_report",
    )
    logger.info(
        "Daily admin report at %02d:%02d Asia/Tehran",
        DAILY_REPORT_HOUR,
        DAILY_REPORT_MINUTE,
    )


def _setup_bonbast_daily_job(application: Application) -> None:
    """EN: Schedule 12:00 Iran daily Bonbast post. FA: زمان‌بندی پست روزانه نرخ."""
    if not BONBAST_DAILY_POST_ENABLED:
        return
    if not application.job_queue:
        logger.warning(
            "JobQueue unavailable — pip install 'python-telegram-bot[job-queue]'. "
            "Bonbast daily post disabled."
        )
        return
    from handlers.bonbast_daily import post_daily_bonbast_rates

    when = time(
        hour=BONBAST_DAILY_HOUR,
        minute=BONBAST_DAILY_MINUTE,
        tzinfo=ZoneInfo("Asia/Tehran"),
    )
    application.job_queue.run_daily(
        post_daily_bonbast_rates,
        time=when,
        name="bonbast_daily_rates",
    )
    logger.info(
        "Bonbast daily rates scheduled at %02d:%02d Asia/Tehran",
        BONBAST_DAILY_HOUR,
        BONBAST_DAILY_MINUTE,
    )


async def _set_bot_command_menus(bot) -> None:
    """EN: Public commands for all; admin extras (admin, post_rates) per ADMIN_IDS."""
    public_cmds = [
        BotCommand("start", "شروع و ثبت‌نام"),
        BotCommand("menu", "نمایش منوی اصلی"),
    ]
    admin_cmds = public_cmds + [
        BotCommand("admin", "پنل مدیریت"),
        BotCommand("neg_ad", "گزارش مذاکرات یک آگهی"),
        BotCommand("post_rates", "ارسال نرخ بن‌بست در کانال"),
        BotCommand("cards", "کارت‌های بانکی (قابل کپی)"),
        BotCommand("txin", "ثبت ورودی در سایت ایران"),
        BotCommand("txout", "ثبت خروجی در سایت ایران"),
    ]
    await bot.set_my_commands(public_cmds)
    for admin_id in set(ADMIN_IDS or []):
        if not admin_id:
            continue
        try:
            await bot.set_my_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=int(admin_id))
            )
        except Exception:
            logger.warning("set_my_commands failed for admin %s", admin_id)


async def admin_post_bonbast_rates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """EN: /post_rates — admin test post. FA: ارسال فوری نرخ برای تست."""
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id not in set(ADMIN_IDS or []):
        return
    from handlers.bonbast_daily import post_bonbast_rates_now

    status = await update.message.reply_text("⏳ در حال دریافت نرخ از bonbast…")
    try:
        await post_bonbast_rates_now(context.bot)
        await status.edit_text("✅ نرخ ارز در کانال منتشر شد.")
    except Exception as exc:
        await status.edit_text(f"❌ خطا در دریافت/ارسال نرخ: {exc}")


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
    from utils.app_logging import setup_app_logging

    setup_app_logging()
    print("🚀 بوت رباط در حال اجراست...")
    try:
        ensure_schema()
        logging.getLogger(__name__).info("ensure_schema completed")
    except Exception:
        logging.getLogger(__name__).exception("ensure_schema failed")
    application = _create_application()

    # Group -1: access gates / گیت غیرفعال بودن، محدودیت و ثبت‌نام
    application.add_handler(MessageHandler(filters.ALL, bot_disabled_gate), group=-1)
    application.add_handler(CallbackQueryHandler(bot_disabled_gate), group=-1)
    application.add_handler(MessageHandler(filters.ALL, restricted_user_gate), group=-1)
    application.add_handler(CallbackQueryHandler(restricted_user_gate), group=-1)
    application.add_handler(MessageHandler(filters.ALL, unregistered_user_gate), group=-1)
    application.add_handler(CallbackQueryHandler(unregistered_user_gate), group=-1)

    # Commands & registration / دستورات و ثبت‌نام
    application.add_handler(CommandHandler("start", handle_welcome))
    application.add_handler(CommandHandler("menu", show_main_menu_command))
    application.add_handler(CommandHandler("admin", admin_entry))
    application.add_handler(CommandHandler("neg_ad", admin_neg_ad_command))
    application.add_handler(CommandHandler("post_rates", admin_post_bonbast_rates_cmd))
    application.add_handler(CommandHandler("cards", admin_cards_command))
    application.add_handler(CommandHandler("txin", txin_command))
    application.add_handler(CommandHandler("txout", txout_command))
    # Inline start/terms (prevents user-message spam)
    application.add_handler(CallbackQueryHandler(handle_channel_member_ack, pattern="^ch_member_ok$"))
    # terms_accept / start_begin → registration_handler
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
    application.add_handler(CallbackQueryHandler(handle_bot_closed_callback, pattern=r"^bot_closed$"))
    application.add_handler(CallbackQueryHandler(handle_noop_callback, pattern=r"^pg\|noop$"))
    application.add_handler(CallbackQueryHandler(handle_my_offers_page_callback, pattern=r"^my_off\|p\|\d+$"))
    application.add_handler(CallbackQueryHandler(handle_services_menu_callback, pattern="^svc_"))

    # دکمه‌های انصراف و بازگشت به منوی اصلی (نه «بازگشت» تنها — در فلو tx گیج‌کننده است)
    application.add_handler(MessageHandler(
        filters.Regex(
            "^(?:❌ بازگشت|بازگشت ❌|❌ بازگشت به منوی اصلی|بازگشت به منوی اصلی ❌|❌ انصراف|انصراف ❌|انصراف|"
            r"بازگشت به منوی اصلی|🏠 بازگشت به منو اصلی)$"
        ),
        handle_cancel
    ))
    # (Service selection is inline now)

    # ورود مرحله‌ای اطلاعات یورو / معاوضه
    _private_text = filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND
    _iran_fill_text = filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND
    _iran_txn = filters.ChatType.PRIVATE & ~filters.COMMAND
    _iran_receipt_media = (
        filters.ChatType.PRIVATE
        & (filters.PHOTO | filters.Document.IMAGE)
        & ~filters.COMMAND
    )
    _deal_receipt_media = (
        filters.ChatType.PRIVATE
        & (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF)
        & ~filters.COMMAND
    )
    # --- Deal Gate | دروازه معامله (groups 0/4) — receipts & accounts before wizard ---
    # EN: See docs/DEAL_GATE.md. FA: ر.ک. docs/DEAL_GATE.md
    # PTB: فقط یک handler در هر group اجرا می‌شود — deal_gate و wizard باید group جدا باشند.
    application.add_handler(MessageHandler(_private_text, deal_gate_group0_text_router), group=0)
    application.add_handler(MessageHandler(_private_text, wizard_text_router), group=1)
    application.add_handler(MessageHandler(_iran_fill_text, iran_panel_fill_router), group=2)
    application.add_handler(
        MessageHandler(_iran_receipt_media, admin_deal_gate_account_photo_router),
        group=3,
    )
    application.add_handler(
        MessageHandler(_deal_receipt_media, deal_gate_group0_photo_router),
        group=4,
    )
    application.add_handler(
        MessageHandler(_iran_receipt_media, iran_panel_sync_router),
        group=5,
    )
    application.add_handler(MessageHandler(_private_text, euro_flow_router), group=6)
    application.add_handler(MessageHandler(_iran_txn, iran_panel_sync_router), group=7)
    # Admin panel: run in later group to avoid hijacking normal flows
    application.add_handler(CallbackQueryHandler(iran_panel_tx_callback, pattern=r"^tx\|"))
    application.add_handler(CallbackQueryHandler(deal_gate_callback, pattern=r"^(deal\||adm\|dg\|)"))
    application.add_handler(MessageHandler(_private_text, admin_router), group=8)

    # تایید نهایی آگهی
    application.add_handler(CallbackQueryHandler(confirm_and_post_advert, pattern="^confirm_advert$"))
    application.add_handler(CallbackQueryHandler(handle_instant_transfer_callback, pattern="^instant_"))

    # معاوضه یورو به یورو
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex("^💱 معاوضه Euro به Euro$"),
            start_exchange_flow,
        )
    )
    application.add_handler(CallbackQueryHandler(handle_exchange_instant_transfer_callback, pattern="^exchange_instant_"))
    application.add_handler(CallbackQueryHandler(handle_exchange_choice, pattern="^exchange_(can_transfer|no_transfer)$"))
    application.add_handler(CallbackQueryHandler(handle_confirm_exchange, pattern="^confirm_exchange$"))
    application.add_handler(CallbackQueryHandler(handle_service_operation_callback, pattern="^service_op_"))
    application.add_handler(CallbackQueryHandler(handle_inline_cancel_callback, pattern="^inline_cancel$"))
    # Deal Gate admin callbacks | callbackهای ادمین معامله: pxy, pay, tomset, eurcfm, stom, outlog
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_party_proxy_callback,
            pattern=r"^adm\|pxy\|",
        )
    )
    application.add_handler(CallbackQueryHandler(deal_admin_payment_callback, pattern=r"^adm\|pay\|"))
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_toman_settled_callback,
            pattern=r"^adm\|tomset\|",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_euro_settled_callback,
            pattern=r"^adm\|eurcfm\|",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_seller_toman_receipt_callback,
            pattern=r"^adm\|stom\|",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_send_buyer_eur_account_callback,
            pattern=r"^adm\|buyeur\|",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            deal_admin_view_outbound_logs_callback,
            pattern=r"^adm\|outlog\|",
        )
    )
    application.add_handler(CallbackQueryHandler(admin_dashboard_callback, pattern=r"^adm\|"))
    application.add_handler(CallbackQueryHandler(bank_cards_callback, pattern=r"^cards\|"))
    application.add_handler(
        CallbackQueryHandler(
            admin_add_user_otp_callback,
            pattern=r"^admin_add_otp_(resend|show)$",
        )
    )
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
    application.add_handler(CallbackQueryHandler(handle_offer_back_euro_amount, pattern=r"^offer_back_euro\|\d+$"))
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
        logger.info(
            "flow_routing: deal_gate_g0 wizard_g1 (PTB one handler per group)"
        )
        await auto_start_notify(app)
        _setup_bonbast_daily_job(app)
        _setup_daily_admin_report_job(app)
        _setup_seller_stom_reminder_job(app)
        try:
            await _set_bot_command_menus(app.bot)
        except Exception:
            logger.exception("set_my_commands failed")

    application.post_init = post_init
    application.run_polling()


if __name__ == "__main__":
    main()
