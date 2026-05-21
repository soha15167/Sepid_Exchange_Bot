"""
utils/channel_membership.py — Channel membership for posting ads / عضویت کانال

EN: Require channel membership to post; unban/re-add on confirm when user was removed.
FA: ثبت آگهی فقط برای اعضای کانال؛ با تأیید، کاربر حذف‌شده دوباره به کانال اضافه می‌شود.
"""

from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import ADVERT_CHANNEL_ID, CHANNEL_USERNAME
from utils.channel_format import format_channel_line_html

logger = logging.getLogger(__name__)

_RTL = "\u200f"

_MEMBER_OK = frozenset(
    {"creator", "administrator", "member", "restricted", "owner"}
)


def _status_name(status) -> str:
    return str(getattr(status, "value", status) or "").lower()


def channel_public_url() -> str | None:
    ch = (CHANNEL_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{ch}" if ch else None


def channel_membership_keyboard() -> InlineKeyboardMarkup:
    """عضویت در کانال + تأیید بعد از join (بازگشت به منوی اصلی)."""
    rows: list[list[InlineKeyboardButton]] = []
    url = channel_public_url()
    if url:
        rows.append([InlineKeyboardButton("📢 عضویت در کانال", url=url)])
    rows.append(
        [InlineKeyboardButton("✅ عضو شدم — بازگشت به منو", callback_data="ch_member_ok")]
    )
    return InlineKeyboardMarkup(rows)


def channel_membership_required_html(*, at_confirm_step: bool = False) -> str:
    """
    EN: HTML when user is not in channel.
    FA: پیام الزام عضویت — بدون اشاره به «تأیید آگهی» مگر در مرحلهٔ پیش‌نمایش.
    """
    ch = (CHANNEL_USERNAME or "").strip().lstrip("@")
    lines = [
        f"{_RTL}❌ برای <b>ثبت آگهی</b> باید عضو کانال باشید.",
    ]
    if at_confirm_step:
        lines.append(
            f"{_RTL}۱) از دکمهٔ زیر در کانال عضو شوید.\n"
            f"۲) برگردید و دکمهٔ <b>✅ تأیید آگهی</b> (در پیش‌نمایش) را بزنید."
        )
    else:
        lines.append(
            f"{_RTL}۱) دکمهٔ «عضویت در کانال» را بزنید.\n"
            f"۲) بعد از عضو شدن «عضو شدم — بازگشت به منو» را بزنید."
        )
    if ch:
        lines.append(format_channel_line_html(ch))
    return "\n".join(lines)


async def ensure_advert_channel_member(bot: Bot, user_id: int) -> tuple[bool, str | None]:
    """
    EN: True if user is in ADVERT_CHANNEL_ID; tries unban for left/kicked then re-checks.
    FA: عضو بودن؛ در صورت left/kicked تلاش برای unban و عضویت مجدد.
    """
    if not ADVERT_CHANNEL_ID:
        return False, (
            "❌ شناسهٔ کانال در تنظیمات (<code>ADVERT_CHANNEL_ID</code>) تعریف نشده است."
        )

    try:
        cid = int(ADVERT_CHANNEL_ID)
        uid = int(user_id)
    except (TypeError, ValueError):
        return False, "❌ شناسهٔ کانال یا کاربر نامعتبر است."

    status = await _fetch_member_status(bot, cid, uid)
    if status in _MEMBER_OK:
        return True, None

    if status in ("left", "kicked"):
        try:
            await bot.unban_chat_member(chat_id=cid, user_id=uid, only_if_banned=True)
        except Exception as exc:
            logger.warning("unban_chat_member failed uid=%s: %s", uid, exc)
        status = await _fetch_member_status(bot, cid, uid)
        if status in _MEMBER_OK:
            return True, None

    return False, channel_membership_required_html(at_confirm_step=False)


async def _fetch_member_status(bot: Bot, channel_id: int, user_id: int) -> str | None:
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return _status_name(member.status)
    except Exception as exc:
        logger.warning("get_chat_member failed ch=%s uid=%s: %s", channel_id, user_id, exc)
        return None
