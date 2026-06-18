"""Channel membership checks for web advert publish."""

from __future__ import annotations

import re

from telegram import Bot

from database.web_auth import is_synthetic_web_user
from utils.channel_membership import channel_public_url, ensure_advert_channel_member


def _html_to_plain(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"<[^>]+>", "", html)
    return text.replace("&nbsp;", " ").strip() or None


async def check_user_can_publish_advert(bot: Bot | None, user_id: int) -> dict:
    """Return membership status for advert publish (mirrors bot euro_flow gate)."""
    ch_url = channel_public_url()
    uid = int(user_id)

    if is_synthetic_web_user(uid):
        return {
            "allowed": False,
            "reason": "web_only",
            "message": (
                "برای انتشار آگهی در کانال باید با همان شماره در ربات تلگرام ثبت‌نام کنید "
                "تا حساب واقعی تلگرام به پروفایل متصل شود."
            ),
            "channel_url": ch_url,
        }

    if uid <= 0:
        return {
            "allowed": False,
            "reason": "invalid_user",
            "message": "شناسه کاربر نامعتبر است.",
            "channel_url": ch_url,
        }

    if not bot:
        return {
            "allowed": True,
            "reason": "no_bot",
            "message": None,
            "channel_url": ch_url,
        }

    ok, err_html = await ensure_advert_channel_member(bot, uid)
    return {
        "allowed": ok,
        "reason": None if ok else "not_member",
        "message": None if ok else _html_to_plain(err_html),
        "channel_url": ch_url,
    }
