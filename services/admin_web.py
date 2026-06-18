"""Web admin actions — same DB/business rules as handlers/admin.py."""

from __future__ import annotations

import sqlite3
from typing import Any

from config.settings import ADVERT_CHANNEL_ID, LIST_RECENT_LIMIT
from database.db import (
    DB_PATH,
    admin_delete_offer_by_id,
    admin_update_offer_proposed_euro,
    admin_update_offer_rate,
    count_euro_adverts,
    count_users,
    daily_stats_since_hours,
    deal_gate_list_for_admin,
    deal_gate_lookup_for_admin,
    delete_user,
    display_name_exists,
    get_db,
    get_euro_advert_by_rowid,
    get_user,
    get_user_by_id,
    get_user_by_phone,
    insert_advert_offer,
    is_bot_enabled,
    is_user_restricted,
    list_advert_offers_joined_for_advert,
    list_euro_adverts_page,
    list_users_page,
    log_admin_action,
    negotiation_transcript_list,
    save_user,
    search_users,
    set_setting,
    set_user_restriction,
    update_user_field,
)
from utils.euro_fees import advert_fee_override_eur, format_fee_eur
from utils.sms import (
    generate_sms_code,
    is_otp_code_valid,
    otp_checked_via_twilio_verify,
    try_send_verification_sms,
)
from utils.validators import is_valid_email, is_valid_phone

_pending_admin_user_otp: dict[str, dict] = {}

ADMIN_MENU = [
    {"id": "users", "label": "👥 لیست کاربران", "web": True},
    {"id": "search_user", "label": "🔎 جستجوی کاربر", "web": True},
    {"id": "offers", "label": "📋 مدیریت پیشنهاد آگهی", "web": True},
    {"id": "add_user", "label": "➕ افزودن کاربر", "web": True},
    {"id": "edit_user", "label": "✏️ ویرایش کاربر", "web": True},
    {"id": "delete_user", "label": "🗑️ حذف کاربر", "web": True},
    {"id": "adverts", "label": "📢 لیست آگهی‌ها", "web": True},
    {"id": "post_advert", "label": "➕ ثبت آگهی", "web": True, "href": "/dashboard/new-advert"},
    {"id": "edit_advert", "label": "✏️ ویرایش آگهی", "web": True},
    {"id": "delete_advert", "label": "🗑️ حذف آگهی", "web": True},
    {"id": "search_advert", "label": "🔎 جستجوی آگهی", "web": True},
    {"id": "negotiations", "label": "🗣️ مذاکرات آگهی", "web": True},
    {"id": "deal_gates", "label": "📊 وضعیت معاملات", "web": True},
    {"id": "restrict", "label": "🔒 محدودیت دسترسی", "web": True},
    {"id": "bot_off", "label": "⛔️ غیرفعال کردن ربات", "web": True},
    {"id": "bot_on", "label": "✅ فعال کردن ربات", "web": True},
    {"id": "proxy_offer", "label": "🎭 پیشنهاد نمایشی", "web": True},
    {"id": "broadcast_rate", "label": "📊 نرخ بن‌بست کانال", "web": True},
    {"id": "restart_bot", "label": "🔄 ری‌استارت سرویس", "web": True},
]

_ROLE_FA = {
    "owner": "آگهی‌دهنده",
    "proposer": "پیشنهاددهنده",
    "system": "سیستم",
    "admin": "ادمین",
    "buyer": "خریدار",
    "seller": "فروشنده",
    "other": "؟",
}

_OFFER_STATUS_FA = {
    "pending": "در انتظار",
    "accepted": "پذیرفته",
    "rejected": "رد شده",
    "withdrawn": "پس‌گرفته",
}


def admin_menu() -> list[dict]:
    return ADMIN_MENU


def admin_stats_payload() -> dict:
    return {
        "bot_enabled": is_bot_enabled(),
        "users_total": count_users(),
        "adverts_total": count_euro_adverts(),
        "last_24h": daily_stats_since_hours(24),
    }


def _user_row_to_dict(row: tuple | sqlite3.Row | dict) -> dict:
    if isinstance(row, dict):
        u = row
    else:
        u = {
            "telegram_id": row[0],
            "username": row[1],
            "display_name": row[2],
            "full_name": row[3],
            "last_name": row[4],
            "phone_number": row[5],
            "email": row[6],
            "address": row[7] if len(row) > 7 else None,
        }
    full = get_user(int(u["telegram_id"])) or {}
    u["is_restricted"] = bool(full.get("is_restricted"))
    u["restricted_until"] = full.get("restricted_until")
    return u


def list_users(*, page: int = 0) -> dict:
    lim = LIST_RECENT_LIMIT
    off = page * lim
    total = count_users()
    pages = max(1, (total + lim - 1) // lim) if total else 1
    rows = list_users_page(limit=lim, offset=off)
    return {
        "items": [_user_row_to_dict(r) for r in rows],
        "page": page,
        "pages": pages,
        "total": total,
    }


def search_users_query(q: str, *, limit: int = 20) -> list[dict]:
    rows = search_users(q, limit=limit)
    return [_user_row_to_dict(r) for r in rows]


def get_user_detail(telegram_id: int) -> dict | None:
    u = get_user(telegram_id)
    if not u:
        return None
    u["is_restricted"] = is_user_restricted(telegram_id)
    return u


def update_user_fields(telegram_id: int, fields: dict[str, str]) -> bool:
    allowed = {"full_name", "last_name", "display_name", "username", "email", "address", "phone_number"}
    ok_any = False
    for k, v in fields.items():
        if k not in allowed:
            continue
        if update_user_field(telegram_id, k, (v or "").strip() or None):
            ok_any = True
    return ok_any


def remove_user(telegram_id: int) -> bool:
    return delete_user(telegram_id)


def set_restrict(telegram_id: int, restricted: bool, until_ts: int | None = None) -> bool:
    return set_user_restriction(telegram_id, restricted, until_ts=until_ts)


def list_adverts(*, page: int = 0) -> dict:
    lim = LIST_RECENT_LIMIT
    off = page * lim
    total = count_euro_adverts()
    pages = max(1, (total + lim - 1) // lim) if total else 1
    rows = list_euro_adverts_page(limit=lim, offset=off)
    items = [
        {
            "id": r[0],
            "owner_name": r[1],
            "username": r[2],
            "euro_amount": r[3],
            "rate_toman": r[4],
            "operation": r[5],
        }
        for r in rows
    ]
    return {"items": items, "page": page, "pages": pages, "total": total}


def get_advert_detail(advert_id: int) -> dict | None:
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv:
        return None
    owner = get_user(int(adv.get("user_id") or 0))
    fee_ov = advert_fee_override_eur(adv)
    amt = int(adv.get("euro_amount") or 0)
    return {
        "id": advert_id,
        "user_id": adv.get("user_id"),
        "owner_name": (owner or {}).get("display_name") or adv.get("full_name"),
        "owner_username": (owner or {}).get("username"),
        "operation": adv.get("operation"),
        "euro_amount": adv.get("euro_amount"),
        "rate_toman": adv.get("rate_toman"),
        "description": adv.get("description"),
        "methods": adv.get("methods"),
        "account_country": adv.get("account_country"),
        "instant_transfer": adv.get("instant_transfer"),
        "fee_override_eur": fee_ov,
        "fee_display": format_fee_eur(amt, fee_ov),
        "channel_message_id": adv.get("channel_message_id"),
        "status": adv.get("status"),
    }


def delete_advert_admin(advert_id: int) -> tuple[bool, int | None]:
    """Returns (deleted, channel_message_id)."""
    with get_db() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT channel_message_id FROM euro_adverts WHERE rowid = ?",
            (advert_id,),
        ).fetchone()
        ch_mid = int(row[0]) if row and row[0] is not None else None
        cur.execute("DELETE FROM offer_negotiation_lines WHERE offer_id IN (SELECT id FROM advert_offers WHERE advert_rowid = ?)", (advert_id,))
        cur.execute("DELETE FROM advert_offers WHERE advert_rowid = ?", (advert_id,))
        cur.execute("DELETE FROM euro_adverts WHERE rowid = ?", (advert_id,))
        deleted = cur.rowcount > 0
    return deleted, ch_mid


def update_advert_fee(advert_id: int, fee_override_eur: float | None) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE euro_adverts SET fee_override_eur = ? WHERE rowid = ?",
            (fee_override_eur, advert_id),
        )
        return cur.rowcount > 0


def update_advert_field(advert_id: int, field: str, value: str) -> bool:
    allowed = frozenset(
        {"euro_amount", "rate_toman", "description", "methods", "account_country", "instant_transfer", "fee_override_eur"}
    )
    if field not in allowed:
        return False
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE euro_adverts SET {field} = ? WHERE rowid = ?", (value, advert_id))
        return cur.rowcount > 0


def list_offers_for_advert(advert_id: int) -> list[dict]:
    rows = list_advert_offers_joined_for_advert(advert_id, limit=None)
    out = []
    for r in rows:
        st = (r.get("status") or "").strip().lower()
        out.append(
            {
                "id": r.get("id"),
                "seq": r.get("seq_in_advert"),
                "proposer_id": r.get("proposer_telegram_id"),
                "rate_toman": r.get("rate_toman"),
                "proposed_euro_amount": r.get("proposed_euro_amount"),
                "description": r.get("description"),
                "status": st,
                "status_fa": _OFFER_STATUS_FA.get(st, st),
            }
        )
    return out


def delete_offer(offer_id: int) -> dict | None:
    return admin_delete_offer_by_id(offer_id)


def update_offer_rate(offer_id: int, rate_toman: int) -> bool:
    return admin_update_offer_rate(offer_id, rate_toman)


def update_offer_proposed_euro(offer_id: int, amount: int | None) -> bool:
    return admin_update_offer_proposed_euro(offer_id, amount)


def negotiations_report(advert_id: int) -> dict:
    offers = list_advert_offers_joined_for_advert(advert_id, limit=None)
    sections: list[dict] = []
    for row in offers:
        oid = int(row["id"])
        seq = int(row.get("seq_in_advert") or oid)
        entries = negotiation_transcript_list(oid)
        lines = [
            {"role": _ROLE_FA.get((e.get("from") or "other"), "؟"), "text": e.get("text") or ""}
            for e in entries
        ]
        st = (row.get("status") or "").strip().lower()
        sections.append(
            {
                "offer_id": oid,
                "seq": seq,
                "status": st,
                "status_fa": _OFFER_STATUS_FA.get(st, st),
                "proposer_id": row.get("proposer_telegram_id"),
                "lines": lines,
            }
        )
    return {"advert_id": advert_id, "offer_count": len(offers), "sections": sections}


def list_deal_gates(*, limit: int = 25) -> list[dict]:
    return deal_gate_list_for_admin(limit=limit)


def lookup_deal_gate(*, offer_id: int | None = None, advert_id: int | None = None) -> dict | None:
    return deal_gate_lookup_for_admin(offer_id=offer_id, advert_rowid=advert_id)


def toggle_bot(*, enabled: bool) -> dict:
    set_setting("bot_enabled", "1" if enabled else "0")
    return {"enabled": enabled, "bot_enabled": is_bot_enabled()}


async def toggle_bot_with_telegram(bot, *, enabled: bool, admin_id: int) -> dict:
    from utils.bot_lifecycle import set_bot_enabled_state

    result = await set_bot_enabled_state(bot, enabled=enabled, admin_telegram_id=admin_id)
    return result


def audit(admin_id: int, action: str, detail: str = "") -> None:
    log_admin_action(admin_id, action, detail)


def create_user_admin(
    *,
    telegram_id: int,
    full_name: str,
    last_name: str,
    display_name: str,
    email: str,
    address: str,
    phone_number: str,
    otp_code: str | None = None,
) -> dict:
    uid = int(telegram_id)
    if uid <= 0:
        return {"ok": False, "error": "آیدی تلگرام باید عدد مثبت باشد."}
    if get_user_by_id(uid):
        return {"ok": False, "error": "این آیدی تلگرام قبلاً ثبت شده است."}
    dn = (display_name or "").strip()
    if len(dn) < 2:
        return {"ok": False, "error": "نام نمایشی کوتاه است."}
    if display_name_exists(dn):
        return {"ok": False, "error": "این نام نمایشی قبلاً استفاده شده است."}
    em = (email or "").strip()
    if not is_valid_email(em):
        return {"ok": False, "error": "ایمیل نامعتبر است."}
    phone = (phone_number or "").strip()
    if not is_valid_phone(phone):
        return {"ok": False, "error": "شماره تلفن نامعتبر است (با +)."}

    if get_user_by_phone(phone):
        try:
            save_user(
                user_id=uid,
                full_name=full_name.strip(),
                last_name=last_name.strip(),
                email=em,
                address=address.strip(),
                phone_number=phone,
                display_name=dn,
                username=None,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "user_id": uid}

    if not (otp_code or "").strip():
        code = generate_sms_code()
        sent = try_send_verification_sms(phone, code)
        otp_key = f"{uid}:{phone}"
        _pending_admin_user_otp[otp_key] = {
            "sms_code": code,
            "otp_verify_twilio": bool(sent and otp_checked_via_twilio_verify()),
        }
        from config.settings import WEB_DEV_OTP_IN_RESPONSE

        show_dev = WEB_DEV_OTP_IN_RESPONSE or not sent
        return {
            "ok": False,
            "needs_otp": True,
            "sms_sent": bool(sent),
            "dev_code": code if show_dev else None,
            "message": "کد تأیید به موبایل ارسال شد." if sent else "پیامک ارسال نشد؛ کد dev در پاسخ.",
        }

    otp_key = f"{uid}:{phone}"
    otp_ctx = _pending_admin_user_otp.get(otp_key)
    if not otp_ctx or not is_otp_code_valid(phone, otp_code.strip(), user_data=otp_ctx):
        return {"ok": False, "error": "کد OTP اشتباه است."}
    _pending_admin_user_otp.pop(otp_key, None)

    try:
        save_user(
            user_id=uid,
            full_name=full_name.strip(),
            last_name=last_name.strip(),
            email=em,
            address=address.strip(),
            phone_number=phone,
            display_name=dn,
            username=None,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "user_id": uid}


async def create_proxy_offer_admin(
    bot,
    *,
    admin_id: int,
    advert_id: int,
    alias: str,
    rate_toman: int,
    description: str,
) -> tuple[bool, str | None]:
    from handlers.offers import dispatch_offer_created_notifications

    advert = get_euro_advert_by_rowid(advert_id)
    if not advert:
        return False, "آگهی پیدا نشد."
    owner_uid = int(advert.get("user_id") or 0)
    if owner_uid == int(admin_id):
        return False, "برای آگهی خودتان نمی‌توانید پیشنهاد نمایشی ثبت کنید."
    alias_clean = (alias or "").strip()
    if len(alias_clean) < 2 or len(alias_clean) > 120:
        return False, "نام نمایشی ۲ تا ۱۲۰ نویسه."
    if rate_toman <= 0:
        return False, "نرخ نامعتبر."
    desc = (description or "").strip()
    if len(desc) < 2 or len(desc) > 3500:
        return False, "توضیحات ۲ تا ۳۵۰۰ نویسه."

    ins = insert_advert_offer(
        advert_id,
        int(admin_id),
        int(rate_toman),
        desc,
        offer_alias_name=alias_clean,
        enforce_rejection_rules=False,
    )
    if ins is None:
        return False, "ذخیره پیشنهاد انجام نشد."
    row_id, offer_seq = ins
    if bot:
        await dispatch_offer_created_notifications(
            bot,
            advert_rowid=advert_id,
            proposer_telegram_id=int(admin_id),
            offer_row_id=row_id,
            offer_seq=int(offer_seq),
            rate_toman=int(rate_toman),
            description=desc,
            public_display_name=alias_clean,
            is_admin_proxy=True,
        )
    return True, None


async def broadcast_bonbast_rates(bot) -> tuple[bool, str | None]:
    if not bot:
        return False, "ربات در دسترس نیست."
    from handlers.bonbast_daily import post_bonbast_rates_now

    try:
        ok = await post_bonbast_rates_now(bot)
    except Exception as exc:
        return False, str(exc)
    return (True, None) if ok else (False, "انتشار نرخ ناموفق بود.")


async def restart_bot_service(bot) -> tuple[bool, str | None]:
    from config.settings import BOT_RESTART_COMMAND

    cmd = (BOT_RESTART_COMMAND or "").strip()
    if not cmd:
        return False, "BOT_RESTART_COMMAND در .env تنظیم نشده."
    from handlers.admin import _schedule_host_service_restart

    _schedule_host_service_restart(bot, cmd)
    return True, None
