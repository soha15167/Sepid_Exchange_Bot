"""
handlers/admin_user_notify.py — اطلاع کاربر/آگهی‌دهنده پس از ویرایش ادمین

EN: DM advert owner on admin advert edits; DM user on profile edits.
FA: پیام به صاحب آگهی/کاربر وقتی ادمین فیلد را عوض می‌کند.
"""

from __future__ import annotations

import html as html_module
import logging

from telegram.constants import ParseMode

from database.db import get_euro_advert_by_rowid
from utils.euro_fees import advert_fee_override_eur, format_fee_eur

logger = logging.getLogger(__name__)

_RTL = "\u200f"

_ADVERT_FIELD_LABELS: dict[str, str] = {
    "full_name": "نام آگهی‌دهنده",
    "euro_amount": "مقدار یورو",
    "rate_toman": "نرخ (تومان)",
    "description": "توضیحات",
    "methods": "روش‌های پرداخت/دریافت",
    "account_country": "کشور حساب (خارج ایران)",
    "instant_transfer": "واریز آنی",
    "fee_override_eur": "کارمزد (هر طرف)",
}

_USER_FIELD_LABELS: dict[str, str] = {
    "display_name": "نام نمایشی آگهی",
    "username": "یوزرنیم تلگرام",
    "full_name": "نام",
    "last_name": "نام خانوادگی",
    "phone_number": "شماره تلفن",
    "email": "ایمیل",
    "address": "آدرس",
}

_INSTANT_FA = {
    "have": "دارم",
    "dont_have": "ندارم",
    "unknown": "اطلاعی ندارم",
}

_EXCHANGE_BUNDLE_FIELDS = (
    "methods",
    "account_country",
    "city_ir",
    "city_int",
    "instant_transfer",
    "description",
)


def _esc(text: object) -> str:
    return html_module.escape(str(text or ""))


def _norm_fee(val: object) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _format_instant(val: object) -> str:
    s = str(val or "").strip()
    if not s:
        return "—"
    return _INSTANT_FA.get(s, s)


def _format_advert_field_value(advert: dict | None, field: str) -> str:
    if not advert:
        return "—"
    if field == "fee_override_eur":
        amt = advert.get("euro_amount")
        try:
            amt_i = int(amt) if amt is not None and str(amt).strip().isdigit() else 0
        except (TypeError, ValueError):
            amt_i = 0
        ov = advert_fee_override_eur(advert)
        if ov is None and advert.get("fee_override_eur") is None:
            return "فرمول خودکار"
        return format_fee_eur(amt_i, ov)
    if field == "instant_transfer":
        return _format_instant(advert.get("instant_transfer"))
    if field in ("euro_amount", "rate_toman"):
        raw = advert.get(field)
        try:
            n = int(raw)
            return f"{n:,}"
        except (TypeError, ValueError):
            return str(raw or "—")
    raw = advert.get(field)
    s = str(raw or "").strip()
    return s if s else "—"


def _fee_change_phrase(old: object, new: object, *, euro_amount: int) -> str:
    o = _norm_fee(old)
    n = _norm_fee(new)
    old_txt = format_fee_eur(euro_amount, o) if o is not None else "فرمول خودکار"
    new_txt = format_fee_eur(euro_amount, n) if n is not None else "فرمول خودکار"
    if o == n:
        return f"کارمزد (هر طرف): {new_txt}"
    if o is None and n is not None:
        return f"کارمزد (هر طرف) توسط ادمین تعیین شد: <b>{_esc(new_txt)}</b> (قبلاً: فرمول خودکار)"
    if o is not None and n is None:
        return (
            f"کارمزد (هر طرف) به <b>فرمول خودکار</b> برگشت "
            f"(قبلاً: {_esc(old_txt)})"
        )
    if o is not None and n is not None:
        if n > o:
            word = "افزایش"
        elif n < o:
            word = "کاهش"
        else:
            word = "تغییر"
        return (
            f"کارمزد (هر طرف) <b>{word} یافت</b>: "
            f"{_esc(old_txt)} ← <b>{_esc(new_txt)}</b>"
        )
    return f"کارمزد (هر طرف): {_esc(new_txt)}"


def _values_equal(field: str, old: object, new: object) -> bool:
    if field == "fee_override_eur":
        return _norm_fee(old) == _norm_fee(new)
    return str(old or "").strip() == str(new or "").strip()


async def _send_user_dm(bot, chat_id: int, html: str) -> None:
    if not chat_id:
        return
    try:
        await bot.send_message(
            int(chat_id),
            html,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("admin_user_notify: send failed uid=%s", chat_id)


async def notify_advert_field_updated(
    bot,
    advert_id: int,
    field: str,
    advert_before: dict | None,
) -> None:
    """Notify advert owner after a single field change."""
    after = get_euro_advert_by_rowid(int(advert_id))
    if not after:
        return
    owner = int(after.get("user_id") or 0)
    if not owner:
        return
    old_raw = advert_before.get(field) if advert_before else None
    new_raw = after.get(field)
    if advert_before and _values_equal(field, old_raw, new_raw):
        return

    label = _ADVERT_FIELD_LABELS.get(field, field)
    aid = int(advert_id)
    try:
        amt_i = int(after.get("euro_amount") or 0)
    except (TypeError, ValueError):
        amt_i = 0

    if field == "fee_override_eur":
        body = _fee_change_phrase(
            advert_before.get("fee_override_eur") if advert_before else None,
            after.get("fee_override_eur"),
            euro_amount=amt_i,
        )
    else:
        old_disp = _format_advert_field_value(advert_before, field)
        new_disp = _format_advert_field_value(after, field)
        body = (
            f"{_RTL}فیلد <b>{_esc(label)}</b>: "
            f"{_esc(old_disp)} ← <b>{_esc(new_disp)}</b>"
        )

    html = (
        f"{_RTL}ℹ️ <b>ویرایش آگهی توسط ادمین</b>\n\n"
        f"{_RTL}آگهی <code>#{aid}</code>\n"
        f"{body}\n\n"
        f"{_RTL}در صورت نیاز منوی «آگهی‌های من» را ببینید."
    )
    await _send_user_dm(bot, owner, html)


async def notify_advert_exchange_bundle_updated(
    bot,
    advert_id: int,
    advert_before: dict | None,
) -> None:
    """Notify after admin reconfigures exchange advert (multi-field)."""
    after = get_euro_advert_by_rowid(int(advert_id))
    if not after or not advert_before:
        return
    owner = int(after.get("user_id") or 0)
    if not owner:
        return

    lines: list[str] = []
    for field in _EXCHANGE_BUNDLE_FIELDS:
        old_disp = _format_advert_field_value(advert_before, field)
        new_disp = _format_advert_field_value(after, field)
        if old_disp == new_disp:
            continue
        label = _ADVERT_FIELD_LABELS.get(field, field)
        lines.append(f"• <b>{_esc(label)}</b>: {_esc(old_disp)} ← <b>{_esc(new_disp)}</b>")

    if not lines:
        return

    aid = int(advert_id)
    html = (
        f"{_RTL}ℹ️ <b>ویرایش آگهی (معاوضه) توسط ادمین</b>\n\n"
        f"{_RTL}آگهی <code>#{aid}</code>\n\n"
        + "\n".join(lines)
        + f"\n\n{_RTL}در صورت نیاز منوی «آگهی‌های من» را ببینید."
    )
    await _send_user_dm(bot, owner, html)


async def notify_user_profile_updated(
    bot,
    user_telegram_id: int,
    field: str,
    user_before: dict | None,
    user_after: dict | None,
) -> None:
    """Notify user when admin edits their profile."""
    if not user_after:
        return
    uid = int(user_telegram_id)
    if not uid:
        return
    old_raw = user_before.get(field) if user_before else None
    new_raw = user_after.get(field)
    if user_before and _values_equal(field, old_raw, new_raw):
        return

    label = _USER_FIELD_LABELS.get(field, field)
    old_disp = str(old_raw or "").strip() or "—"
    new_disp = str(new_raw or "").strip() or "—"
    if field == "username" and new_disp not in ("—", ""):
        new_disp = f"@{new_disp.lstrip('@')}"
    if field == "username" and old_disp not in ("—", ""):
        old_disp = f"@{old_disp.lstrip('@')}"

    html = (
        f"{_RTL}ℹ️ <b>ویرایش مشخصات توسط ادمین</b>\n\n"
        f"{_RTL}فیلد <b>{_esc(label)}</b>:\n"
        f"{_esc(old_disp)} ← <b>{_esc(new_disp)}</b>\n\n"
        f"{_RTL}در صورت اشتباه با پشتیبانی تماس بگیرید."
    )
    await _send_user_dm(bot, uid, html)
