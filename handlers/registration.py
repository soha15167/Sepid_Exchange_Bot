"""
handlers/registration.py — User signup / ثبت‌نام کاربر

EN: ConversationHandler — name, display name, email, address, phone, SMS code.
FA: فلو چندمرحله‌ای تا ذخیره در جدول users؛ نام نمایشی یکتا در آگهی.
"""

import html as html_module
import re

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
from utils.validators import (
    is_valid_email,
    is_valid_phone,
    normalize_phone_input,
    phone_starts_with_plus,
    registration_phone_error_html,
    registration_phone_prompt_html,
)
from utils.sms import (
    generate_sms_code,
    is_otp_code_valid,
    try_send_verification_sms,
    otp_checked_via_twilio_verify,
)

_RTL = "\u200f"
from keyboards.menus import (
    registration_cancel_inline_keyboard,
    registration_otp_keyboard,
)
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
from keyboards.menus import REGISTRATION_START_BUTTON_TEXT, reply_menu_text_matches
from utils.telegram_utils import (
    REGISTRATION_CLEANUP_KEY,
    cleanup_transient_dm_messages,
    mark_flow_keep_message,
    normalize_telegram_callback_data,
    pop_menu_anchor_message_id,
    purge_all_trackable_dm_messages,
    reset_flow_message_tracking,
    send_or_replace_main_menu,
    send_registration_terms,
    send_registration_welcome,
    track_flow_message,
    track_flow_user_message,
)

REGISTER_FULLNAME, REGISTER_LASTNAME, REGISTER_DISPLAY_NAME, REGISTER_EMAIL, REGISTER_ADDRESS, REGISTER_PHONE, VERIFY_CODE = range(7)

OTP_TELEGRAM_REVEAL_SECONDS = 60

_DISPLAY_NAME_PROMPT_HTML = (
    f"{_RTL}🏷️ <b>نام نمایشی در آگهی</b> را وارد کنید.\n"
    f"{_RTL}<i>همان نامی که دیگران در آگهی می‌بینند؛ باید یکتا باشد.</i>"
)


def _otp_countdown_job_name(user_id: int) -> str:
    return f"reg_otp_countdown_{user_id}"


def _cancel_otp_countdown_jobs(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    jq = getattr(context.application, "job_queue", None)
    if not jq:
        return
    for job in jq.get_jobs_by_name(_otp_countdown_job_name(user_id)):
        job.schedule_removal()


def _clear_otp_flow_extras(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    _cancel_otp_countdown_jobs(context, user_id)
    for key in (
        "otp_prompt_message_id",
        "otp_telegram_option_visible",
        "otp_countdown_remaining",
    ):
        context.user_data.pop(key, None)


async def _otp_apply_countdown_markup(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    message_id: int,
    remaining: int,
    show_telegram: bool,
) -> None:
    markup = registration_otp_keyboard(
        show_telegram=show_telegram,
        countdown=remaining if not show_telegram and remaining >= 0 else None,
    )
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=int(message_id),
            reply_markup=markup,
        )
    except Exception:
        pass


async def _otp_countdown_tick_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ثانیه شمارندهٔ اینلاین را کم کن؛ در ۰ دکمهٔ تلگرام را نشان بده."""
    job = context.job
    if not job:
        return
    user_id = job.user_id
    chat_id = job.chat_id
    if not user_id or not chat_id:
        return
    if not context.user_data.get("registration_active"):
        _cancel_otp_countdown_jobs(context, user_id)
        return
    if context.user_data.get("otp_telegram_sent"):
        _cancel_otp_countdown_jobs(context, user_id)
        return

    mid = context.user_data.get("otp_prompt_message_id") or (job.data or {}).get("prompt_mid")
    if not mid:
        return

    remaining = context.user_data.get("otp_countdown_remaining")
    if remaining is None:
        remaining = OTP_TELEGRAM_REVEAL_SECONDS

    if remaining < 0 or context.user_data.get("otp_telegram_option_visible"):
        context.user_data["otp_telegram_option_visible"] = True
        await _otp_apply_countdown_markup(
            context, chat_id=chat_id, message_id=int(mid), remaining=-1, show_telegram=True
        )
        _cancel_otp_countdown_jobs(context, user_id)
        return

    await _otp_apply_countdown_markup(
        context,
        chat_id=chat_id,
        message_id=int(mid),
        remaining=int(remaining),
        show_telegram=False,
    )
    context.user_data["otp_countdown_remaining"] = int(remaining) - 1


def _schedule_otp_countdown(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
    prompt_message_id: int,
) -> None:
    _cancel_otp_countdown_jobs(context, user_id)
    context.user_data["otp_prompt_message_id"] = prompt_message_id
    context.user_data["otp_telegram_option_visible"] = False
    context.user_data["otp_countdown_remaining"] = OTP_TELEGRAM_REVEAL_SECONDS
    jq = getattr(context.application, "job_queue", None)
    if not jq:
        context.user_data["otp_telegram_option_visible"] = True
        return
    jq.run_repeating(
        _otp_countdown_tick_job,
        interval=1,
        first=1,
        chat_id=chat_id,
        user_id=user_id,
        name=_otp_countdown_job_name(user_id),
        data={"prompt_mid": prompt_message_id},
    )


async def _reg_cleanup_messages(
    bot,
    *,
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    extra_ids: list[int] | None = None,
) -> None:
    ids: list[int] = list(extra_ids or [])
    anchor_mid = pop_menu_anchor_message_id(user_data_store, user_id)
    if anchor_mid is not None:
        ids.append(anchor_mid)
    context.user_data.pop("flow_keep_message_ids", None)
    bucket = user_data_store.setdefault(user_id, {})
    bucket.pop("flow_keep_message_ids", None)
    await cleanup_transient_dm_messages(
        bot,
        chat_id=chat_id,
        user_id=user_id,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=ids,
        keep_message_ids=[],
    )


async def _registration_send_telegram_otp(
    bot,
    *,
    chat_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    code: str,
) -> None:
    """ارسال کد در چت تلگرام — تأیید با همان کد محلی (نه Twilio Verify)."""
    _cancel_otp_countdown_jobs(context, user_id)
    context.user_data["otp_telegram_option_visible"] = True
    context.user_data["sms_code"] = code
    context.user_data["otp_telegram_sent"] = True
    context.user_data["otp_delivery"] = "telegram"
    context.user_data["otp_verify_twilio"] = False
    sent = await bot.send_message(
        chat_id=chat_id,
        text=(
            f"{_RTL}به کانال @Sepid_Exchange خوش آمدید.\n"
            f"{_RTL}کد تأیید ثبت‌نام شما: "
            f"<code>{html_module.escape(str(code))}</code>"
        ),
        parse_mode="HTML",
        reply_markup=registration_cancel_inline_keyboard,
    )
    track_flow_message(
        user_data_store, user_id, context.user_data, sent.message_id, key=REGISTRATION_CLEANUP_KEY
    )


async def _reg_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
):
    user_id = update.effective_user.id
    track_flow_user_message(
        update, user_data_store, user_id, context.user_data, key=REGISTRATION_CLEANUP_KEY
    )
    msg = update.message
    if msg is None and update.callback_query is not None:
        msg = update.callback_query.message
    if msg is None:
        # No message to reply to (rare edge case) — avoid crashing.
        return None
    kwargs: dict = {
        "text": text,
        "reply_markup": reply_markup or registration_cancel_inline_keyboard,
    }
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    sent = await msg.reply_text(**kwargs)
    track_flow_message(
        user_data_store, user_id, context.user_data, sent.message_id, key=REGISTRATION_CLEANUP_KEY
    )
    return sent


async def _reply_registration_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    await _reg_reply(
        update, context, text, parse_mode=parse_mode, reply_markup=reply_markup
    )


async def _registration_callback_precheck(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """True = باید متوقف شود (کاربر محدود یا قبلاً ثبت‌نام کرده)."""
    q = update.callback_query
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
            return True

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
        pending_offer = context.user_data.pop("pending_offer_advert_id", None)
        if pending_offer is not None:
            try:
                from handlers.offers import deliver_offer_proposal_gate

                context.user_data["state"] = UserState.OFFER_ADVERT_ID.name
                await deliver_offer_proposal_gate(context, user_id, int(pending_offer))
                return True
            except (TypeError, ValueError):
                pass
        await send_or_replace_main_menu(
            context.bot,
            chat_id=cid,
            user_id=user_id,
            store=user_data_store,
            text="ℹ️ شما قبلاً ثبت‌نام کرده‌اید.",
        )
        context.user_data["state"] = UserState.MAIN_MENU.name
        return True
    return False


async def registration_start_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمهٔ «ثبت‌نام» — فقط نمایش قوانین."""
    q = update.callback_query
    if not q or normalize_telegram_callback_data(q.data) != "start_begin":
        return ConversationHandler.END
    if await _registration_callback_precheck(update, context):
        return ConversationHandler.END
    try:
        await q.answer()
    except Exception:
        pass
    chat_id = q.message.chat_id if q.message else update.effective_chat.id
    uid = update.effective_user.id
    extra: list[int] = []
    if q.message:
        extra.append(q.message.message_id)
    await purge_all_trackable_dm_messages(
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    await send_registration_terms(
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        store=user_data_store,
        context=context,
    )
    return ConversationHandler.END


async def terms_accept_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """«قبول قوانین» — شروع فرم ثبت‌نام."""
    q = update.callback_query
    if not q or normalize_telegram_callback_data(q.data) != "terms_accept":
        return ConversationHandler.END
    if await _registration_callback_precheck(update, context):
        return ConversationHandler.END

    try:
        await q.answer()
    except Exception:
        pass
    uid = update.effective_user.id
    cid = update.effective_chat.id
    extra: list[int] = []
    if q.message:
        extra.append(q.message.message_id)
    await purge_all_trackable_dm_messages(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
        context_user_data=context.user_data,
        extra_message_ids=extra,
    )
    reset_flow_message_tracking(user_data_store, uid, context.user_data)
    context.user_data["registration_active"] = True
    context.user_data["state"] = UserState.FIRST_NAME.name
    try:
        if q.message:
            await q.message.delete()
    except Exception:
        pass
    try:
        rm = await context.bot.send_message(
            chat_id=cid,
            text="\u2060",
            reply_markup=ReplyKeyboardRemove(),
        )
        await rm.delete()
    except Exception:
        pass
    sent = await context.bot.send_message(
        chat_id=cid,
        text=f"{_RTL}👤 لطفاً نام خود را وارد کنید:",
        parse_mode="HTML",
        reply_markup=registration_cancel_inline_keyboard,
    )
    track_flow_message(
        user_data_store, uid, context.user_data, sent.message_id, key=REGISTRATION_CLEANUP_KEY
    )
    return REGISTER_FULLNAME


async def _begin_registration_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دکمهٔ متنی «ثبت‌نام» — ابتدا قوانین، نه فرم."""
    user_id = update.effective_user.id
    if get_user_by_id(user_id) or get_user(user_id):
        await send_or_replace_main_menu(
            context.bot,
            chat_id=update.effective_chat.id,
            user_id=user_id,
            store=user_data_store,
            text="ℹ️ شما قبلاً ثبت‌نام کرده‌اید.",
        )
        return ConversationHandler.END
    await send_registration_terms(
        context.bot,
        chat_id=update.effective_chat.id,
        user_id=user_id,
        store=user_data_store,
    )
    return ConversationHandler.END


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip() if update.message else ""
    if not (
        reply_menu_text_matches(REGISTRATION_START_BUTTON_TEXT, text)
        or text in ("ثبت نام", "ثبت‌نام")
    ):
        return ConversationHandler.END
    return await _begin_registration_form(update, context)


async def get_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reply_registration_step(update, context, f"{_RTL}❌ لطفاً نام خود را به صورت متن ارسال کنید:")
        return REGISTER_FULLNAME
    context.user_data["full_name"] = (update.message.text or "").strip()
    await _reply_registration_step(
        update, context, f"{_RTL}👤 حالا نام خانوادگی خود را وارد کنید:"
    )
    return REGISTER_LASTNAME


async def get_lastname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reply_registration_step(update, context, f"{_RTL}❌ لطفاً نام خانوادگی را به صورت متن ارسال کنید:")
        return REGISTER_LASTNAME
    context.user_data["last_name"] = (update.message.text or "").strip()
    await _reply_registration_step(
        update,
        context,
        _DISPLAY_NAME_PROMPT_HTML,
        parse_mode="HTML",
    )
    return REGISTER_DISPLAY_NAME


async def get_display_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reply_registration_step(
            update,
            context,
            f"{_RTL}❌ لطفاً نام نمایشی را به صورت متن ارسال کنید:",
        )
        return REGISTER_DISPLAY_NAME
    display_name = (update.message.text or "").strip()
    if not display_name:
        await _reply_registration_step(
            update, context, f"{_RTL}❌ لطفاً یک نام معتبر وارد کنید:"
        )
        return REGISTER_DISPLAY_NAME
    if "<" in display_name or ">" in display_name:
        await _reply_registration_step(
            update,
            context,
            f"{_RTL}❌ نام نمایشی نباید شامل < یا > باشد.\n"
            f"{_RTL}مثال درست: <code>n.t</code>",
            parse_mode="HTML",
        )
        return REGISTER_DISPLAY_NAME
    if display_name_exists(display_name):
        await _reply_registration_step(
            update,
            context,
            f"{_RTL}❌ این نام قبلاً استفاده شده است.\n"
            f"{_RTL}لطفاً یک نام دیگر وارد کنید:",
        )
        return REGISTER_DISPLAY_NAME
    context.user_data["display_name"] = display_name
    await _reply_registration_step(
        update, context, f"{_RTL}📧 لطفاً آدرس ایمیل خود را وارد کنید:"
    )
    return REGISTER_EMAIL


async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reply_registration_step(update, context, f"{_RTL}❌ لطفاً ایمیل را به صورت متن ارسال کنید:")
        return REGISTER_EMAIL
    text = (update.message.text or "").strip()
    if not is_valid_email(text):
        await _reply_registration_step(
            update, context, f"{_RTL}❌ آدرس ایمیل نامعتبر است. لطفاً دوباره وارد کنید:"
        )
        return REGISTER_EMAIL
    context.user_data["email"] = text
    await _reply_registration_step(
        update, context, f"{_RTL}🏠 لطفاً آدرس محل زندگی خود را وارد کنید:"
    )
    return REGISTER_ADDRESS


async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reply_registration_step(update, context, f"{_RTL}❌ لطفاً آدرس را به صورت متن ارسال کنید:")
        return REGISTER_ADDRESS
    context.user_data["address"] = (update.message.text or "").strip()
    await _reply_registration_step(
        update,
        context,
        registration_phone_prompt_html(),
        parse_mode="HTML",
    )
    return REGISTER_PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reg_reply(update, context, registration_phone_error_html(), parse_mode="HTML")
        return REGISTER_PHONE
    raw = (update.message.text or "").strip()
    if not phone_starts_with_plus(raw):
        await _reg_reply(
            update,
            context,
            registration_phone_error_html(),
            parse_mode="HTML",
        )
        return REGISTER_PHONE
    phone = normalize_phone_input(raw)
    if not is_valid_phone(phone):
        await _reg_reply(
            update,
            context,
            registration_phone_error_html(),
            parse_mode="HTML",
        )
        return REGISTER_PHONE

    if get_user_by_phone(phone):
        uid = update.effective_user.id
        cid = update.effective_chat.id
        track_flow_user_message(
            update, user_data_store, uid, context.user_data, key=REGISTRATION_CLEANUP_KEY
        )
        await _reg_cleanup_messages(context.bot, chat_id=cid, user_id=uid, context=context)
        await send_registration_welcome(
            context.bot,
            chat_id=cid,
            user_id=uid,
            store=user_data_store,
        )
        return ConversationHandler.END

    uid = update.effective_user.id
    from messages.user_errors import RATE_LIMIT_OTP
    from utils.rate_limit import check_rate_limit, otp_bucket

    if not check_rate_limit(otp_bucket(uid), max_events=6, window_sec=3600):
        await _reg_reply(
            update,
            context,
            RATE_LIMIT_OTP,
            parse_mode="HTML",
        )
        return REGISTER_PHONE

    context.user_data["phone_number"] = phone
    code = generate_sms_code()
    context.user_data["sms_code"] = code
    context.user_data.pop("otp_telegram_sent", None)
    context.user_data.pop("otp_telegram_option_visible", None)
    context.user_data["otp_resend_tried"] = False
    uid = update.effective_user.id
    cid = update.effective_chat.id
    _cancel_otp_countdown_jobs(context, uid)

    otp_markup = registration_otp_keyboard(
        show_telegram=False, countdown=OTP_TELEGRAM_REVEAL_SECONDS
    )

    if try_send_verification_sms(phone, code):
        context.user_data["otp_delivery"] = "sms"
        context.user_data["otp_verify_twilio"] = otp_checked_via_twilio_verify()
        sms_hint = (
            f"{_RTL}📨 کد تأیید به <b>خط موبایل</b> شما پیامک شد.\n"
            f"{_RTL}لطفاً همان کد را اینجا وارد کنید:"
        )
    else:
        context.user_data["otp_delivery"] = "sms_failed"
        context.user_data["otp_verify_twilio"] = False
        sms_hint = (
            f"{_RTL}⚠️ ارسال پیامک انجام نشد.\n"
            f"{_RTL}می‌توانید «ارسال مجدد پیامک» را بزنید."
        )

    sent = await _reg_reply(
        update,
        context,
        sms_hint,
        parse_mode="HTML",
        reply_markup=otp_markup,
    )
    _schedule_otp_countdown(
        context, chat_id=cid, user_id=uid, prompt_message_id=sent.message_id
    )
    return VERIFY_CODE


async def registration_otp_resend_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or (q.data or "") != "reg_otp_resend_sms":
        return VERIFY_CODE
    if not context.user_data.get("otp_telegram_option_visible"):
        rem = context.user_data.get("otp_countdown_remaining")
        try:
            if isinstance(rem, int) and rem >= 0:
                await q.answer(
                    f"تا پایان شمارنده ({rem + 1} ثانیه) ارسال مجدد پیامک غیرفعال است.",
                    show_alert=True,
                )
            else:
                await q.answer(
                    "تا پایان شمارنده ارسال مجدد پیامک غیرفعال است.",
                    show_alert=True,
                )
        except Exception:
            pass
        return VERIFY_CODE
    try:
        await q.answer()
    except Exception:
        pass
    context.user_data["otp_resend_tried"] = True
    phone = context.user_data.get("phone_number") or ""
    code = context.user_data.get("sms_code") or generate_sms_code()
    context.user_data["sms_code"] = code
    uid = q.from_user.id
    cid = q.message.chat_id if q.message else update.effective_chat.id
    context.user_data["otp_telegram_option_visible"] = False
    otp_markup = registration_otp_keyboard(
        show_telegram=False, countdown=OTP_TELEGRAM_REVEAL_SECONDS
    )
    if try_send_verification_sms(phone, code):
        context.user_data["otp_delivery"] = "sms"
        context.user_data["otp_verify_twilio"] = otp_checked_via_twilio_verify()
        msg = (
            f"{_RTL}📨 پیامک دوباره ارسال شد.\n"
            f"{_RTL}لطفاً کد را اینجا وارد کنید:"
        )
    else:
        context.user_data["otp_delivery"] = "sms_failed"
        context.user_data["otp_verify_twilio"] = False
        msg = (
            f"{_RTL}⚠️ ارسال مجدد پیامک ممکن نشد.\n"
            f"{_RTL}پس از پایان شمارنده می‌توانید دوباره تلاش کنید."
        )
    await q.edit_message_text(msg, parse_mode="HTML", reply_markup=otp_markup)
    if q.message:
        _schedule_otp_countdown(
            context, chat_id=cid, user_id=uid, prompt_message_id=q.message.message_id
        )
    return VERIFY_CODE


async def registration_otp_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or (q.data or "") != "reg_otp_telegram":
        return VERIFY_CODE
    if not context.user_data.get("otp_telegram_option_visible"):
        rem = context.user_data.get("otp_countdown_remaining")
        try:
            if isinstance(rem, int) and rem >= 0:
                await q.answer(f"پس از {rem + 1} ثانیه این گزینه فعال می‌شود.", show_alert=True)
            else:
                await q.answer("پس از پایان شمارنده این گزینه فعال می‌شود.", show_alert=True)
        except Exception:
            pass
        return VERIFY_CODE
    try:
        await q.answer()
    except Exception:
        pass
    if context.user_data.get("otp_telegram_sent"):
        await q.edit_message_text(
            f"{_RTL}ℹ️ کد قبلاً در همین چت فرستاده شده؛ همان را وارد کنید.",
            parse_mode="HTML",
        )
        return VERIFY_CODE
    code = context.user_data.get("sms_code") or generate_sms_code()
    context.user_data["sms_code"] = code
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await q.edit_message_text(
        f"{_RTL}📲 کد در <b>تلگرام</b> ارسال شد — در پیام بعدی است:",
        parse_mode="HTML",
        reply_markup=registration_otp_keyboard(show_telegram=True),
    )
    if q.message:
        track_flow_message(
            user_data_store, uid, context.user_data, q.message.message_id, key=REGISTRATION_CLEANUP_KEY
        )
    await _registration_send_telegram_otp(
        context.bot, chat_id=cid, user_id=uid, context=context, code=str(code)
    )
    return VERIFY_CODE


async def registration_otp_wait_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمهٔ شمارنده — فقط بستن loading؛ بدون اثر."""
    q = update.callback_query
    if not q or (q.data or "") != "reg_otp_wait":
        return VERIFY_CODE
    try:
        await q.answer()
    except Exception:
        pass
    return VERIFY_CODE


async def registration_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return VERIFY_CODE
    if normalize_telegram_callback_data(q.data) != "reg_cancel":
        return VERIFY_CODE
    try:
        await q.answer()
    except Exception:
        pass
    return await cancel_registration(update, context)


async def verify_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await _reg_reply(update, context, f"{_RTL}❌ لطفاً کد تأیید را به صورت متن ارسال کنید:")
        return VERIFY_CODE
    raw = (update.message.text or "").strip()
    phone = context.user_data.get("phone_number") or ""
    if not is_otp_code_valid(phone, raw, user_data=context.user_data):
        await _reg_reply(
            update,
            context,
            f"{_RTL}❌ کد اشتباه است. لطفاً دوباره تلاش کنید:",
        )
        return VERIFY_CODE

    uid = update.effective_user.id
    cid = update.effective_chat.id
    _clear_otp_flow_extras(context, uid)
    track_flow_user_message(
        update, user_data_store, uid, context.user_data, key=REGISTRATION_CLEANUP_KEY
    )
    save_user(
        user_id=uid,
        full_name=context.user_data["full_name"],
        last_name=context.user_data["last_name"],
        email=context.user_data["email"],
        address=context.user_data["address"],
        phone_number=context.user_data["phone_number"],
        display_name=context.user_data.get("display_name"),
        username=update.effective_user.username,
    )
    context.user_data.pop("registration_active", None)
    context.user_data.pop("registration", None)
    pending_offer = context.user_data.pop("pending_offer_advert_id", None)
    await _reg_cleanup_messages(context.bot, chat_id=cid, user_id=uid, context=context)
    if pending_offer is not None:
        try:
            from handlers.offers import deliver_offer_proposal_gate

            context.user_data["state"] = UserState.OFFER_ADVERT_ID.name
            await deliver_offer_proposal_gate(context, uid, int(pending_offer))
            return ConversationHandler.END
        except (TypeError, ValueError):
            pass

    context.user_data["state"] = UserState.MAIN_MENU.name
    welcome_txt = (
        f"{_RTL}✅ <b>ثبت‌نام شما با موفقیت انجام شد.</b>\n\n"
        f"{_RTL}قبل از «🚀 درخواست خدمات»، یک‌بار از منوی پایین "
        f"«قوانین و روال کار کانال» را بخوانید؛ بعد از آن درخواست فعال می‌شود."
    )
    menu_mid = await send_or_replace_main_menu(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
        text=welcome_txt,
        parse_mode="HTML",
    )
    mark_flow_keep_message(user_data_store, uid, context.user_data, menu_mid)
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    extra: list[int] = []
    q = update.callback_query
    if q and q.message:
        cid = q.message.chat_id
        extra.append(q.message.message_id)
    _clear_otp_flow_extras(context, uid)
    context.user_data.pop("registration_active", None)
    context.user_data.pop("registration", None)
    await _reg_cleanup_messages(
        context.bot, chat_id=cid, user_id=uid, context=context, extra_ids=extra
    )
    try:
        rm = await context.bot.send_message(
            chat_id=cid, text="\u2060", reply_markup=ReplyKeyboardRemove()
        )
        await rm.delete()
    except Exception:
        pass
    await send_registration_welcome(
        context.bot,
        chat_id=cid,
        user_id=uid,
        store=user_data_store,
    )
    context.user_data["state"] = UserState.START.name
    return ConversationHandler.END


_registration_btn_re = (
    rf"^({re.escape(REGISTRATION_START_BUTTON_TEXT)}|ثبت[\s\u200c]*نام)$"
)


def _registration_start_begin_callback(callback_data: str) -> bool:
    return normalize_telegram_callback_data(callback_data) == "start_begin"


def _terms_accept_callback(callback_data: str) -> bool:
    return normalize_telegram_callback_data(callback_data) == "terms_accept"


registration_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex(_registration_btn_re), start_registration),
        CallbackQueryHandler(registration_start_begin, _registration_start_begin_callback),
        CallbackQueryHandler(terms_accept_entry, _terms_accept_callback),
    ],
    name="registration",
    allow_reentry=True,
    states={
        REGISTER_FULLNAME: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_fullname),
        ],
        REGISTER_LASTNAME: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_lastname),
        ],
        REGISTER_DISPLAY_NAME: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_display_name),
        ],
        REGISTER_EMAIL: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_email),
        ],
        REGISTER_ADDRESS: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_address),
        ],
        REGISTER_PHONE: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
        ],
        VERIFY_CODE: [
            CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
            CallbackQueryHandler(registration_otp_wait_callback, pattern="^reg_otp_wait$"),
            CallbackQueryHandler(registration_otp_telegram, pattern="^reg_otp_telegram$"),
            CallbackQueryHandler(registration_otp_resend_sms, pattern="^reg_otp_resend_sms$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, verify_sms),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_registration),
        CallbackQueryHandler(registration_cancel_callback, pattern="^reg_cancel$"),
    ],
)
