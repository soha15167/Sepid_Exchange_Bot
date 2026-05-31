"""
handlers/channel_gate.py — Channel membership ack / تأیید عضویت کانال

EN: After user joins channel, «عضو شدم» clears block message and shows main menu.
FA: پس از عضویت، پیام خطا حذف و منوی اصلی نمایش داده می‌شود.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from models.enums import UserState
from state import user_data_store
from utils.channel_membership import ensure_advert_channel_member
from utils.telegram_utils import cleanup_ids, send_or_replace_main_menu


async def handle_channel_member_ack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback: ch_member_ok — بررسی مجدد عضویت، حذف پیام خطا، منوی اصلی."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    uid = int(query.from_user.id)
    chat_id = query.message.chat_id if query.message else update.effective_chat.id

    ok, _ = await ensure_advert_channel_member(context.bot, uid)
    if not ok:
        try:
            await query.answer(
                "هنوز عضو کانال نشده‌اید. ابتدا «عضویت در کانال» را بزنید.",
                show_alert=True,
            )
        except Exception:
            pass
        return

    try:
        await query.answer("✅ عضویت تأیید شد.")
    except Exception:
        pass

    extra_mids: list[int] = []
    block_mid = context.user_data.pop("channel_member_block_mid", None)
    if block_mid:
        extra_mids.append(int(block_mid))
    svc_mid = context.user_data.pop("services_menu_message_id", None)
    if svc_mid:
        extra_mids.append(int(svc_mid))
    if query.message:
        extra_mids.append(int(query.message.message_id))

    await cleanup_ids(context.bot, chat_id=chat_id, ids=extra_mids)

    bucket = user_data_store.get(uid, {})
    bucket.pop("euro_cleanup_message_ids", None)
    bucket.pop("exchange_cleanup_message_ids", None)
    bucket["methods"] = []
    bucket["operation"] = ""

    context.user_data.clear()
    context.user_data["state"] = UserState.MAIN_MENU.name

    await send_or_replace_main_menu(
        context.bot,
        chat_id=chat_id,
        user_id=uid,
        store=user_data_store,
        text=(
            "✅ عضویت شما در کانال تأیید شد.\n\n"
            "از منوی زیر «🚀 درخواست خدمات» را بزنید تا آگهی ثبت کنید."
        ),
        parse_mode=ParseMode.HTML,
    )
