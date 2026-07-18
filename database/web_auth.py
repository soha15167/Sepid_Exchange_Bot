"""
database/web_auth.py — Web auth helpers (additive; bot uses same users table).

EN: Password hashing, OTP challenges, synthetic IDs for web-only users, lookups.
FA: احراز هویت وب؛ کاربران فقط-وب با telegram_id منفی (ربات دست نمی‌زند).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from typing import Any

from config.settings import ADMIN_IDS, DB_PATH
from database.db import _table_columns, display_name_exists, get_user, save_user
from utils.validators import is_valid_email, is_valid_phone, normalize_phone_input

WEB_SYNTHETIC_ID_FLOOR = -9_000_000_000
CHALLENGE_TTL_SEC = 600


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _ascii_digits(raw: str) -> str:
    s = (raw or "").strip()
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return "".join(ch for ch in s if ch.isdigit())


def _iran_mobile_suffix(digits: str) -> str | None:
    """10-digit Iran mobile without country code (9xxxxxxxxx)."""
    d = _ascii_digits(digits)
    if len(d) == 11 and d.startswith("09"):
        return d[1:]
    if len(d) == 10 and d.startswith("9"):
        return d
    if len(d) >= 12 and d.startswith("98"):
        tail = d[2:]
        if len(tail) == 10 and tail.startswith("9"):
            return tail
    return None


def normalize_lookup_phone(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("+"):
        return normalize_phone_input(s)
    digits = _ascii_digits(s)
    if not digits:
        return ""
    if digits.startswith("00"):
        return normalize_phone_input("+" + digits[2:])
    iran = _iran_mobile_suffix(digits)
    if iran:
        return normalize_phone_input("+98" + iran)
    if digits.startswith("98") and len(digits) >= 12:
        return normalize_phone_input("+" + digits)
    if digits.startswith("0") and len(digits) >= 10:
        return normalize_phone_input("+98" + digits[1:])
    if digits:
        return normalize_phone_input("+" + digits)
    return ""


def _phone_match_keys(phone: str) -> set[str]:
    """Keys for fuzzy phone lookup (exact E.164 + Iran mobile suffix)."""
    keys: set[str] = set()
    norm = normalize_lookup_phone(phone)
    if norm:
        keys.add(norm)
        keys.add(_ascii_digits(norm))
    raw_digits = _ascii_digits(phone)
    if raw_digits:
        keys.add(raw_digits)
    iran = _iran_mobile_suffix(phone)
    if iran:
        keys.add(iran)
        keys.add("98" + iran)
        keys.add("+98" + iran)
    return {k for k in keys if k}


def find_user_by_phone(phone: str) -> dict | None:
    p = normalize_lookup_phone(phone)
    keys = _phone_match_keys(phone)
    if not keys and not p:
        return None
    with _connect() as conn:
        if p and is_valid_phone(p):
            row = conn.execute(
                "SELECT * FROM users WHERE phone_number = ? ORDER BY rowid DESC LIMIT 1",
                (p,),
            ).fetchone()
            if row:
                return dict(row)
        iran = _iran_mobile_suffix(phone) or (p and _iran_mobile_suffix(p))
        if iran:
            like = f"%{iran}"
            row = conn.execute(
                """
                SELECT * FROM users
                WHERE phone_number IS NOT NULL AND TRIM(phone_number) != ''
                  AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone_number, ''), ' ', ''), '-', ''), '+', ''), '۰', '0') LIKE ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (like,),
            ).fetchone()
            if row:
                return dict(row)
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE phone_number IS NOT NULL AND TRIM(phone_number) != ''
            ORDER BY rowid DESC
            """
        ).fetchall()
        for row in rows:
            rec = dict(row)
            stored = rec.get("phone_number") or ""
            stored_keys = _phone_match_keys(stored)
            if keys & stored_keys:
                return rec
    return None


def find_user_by_email(email: str) -> dict | None:
    em = normalize_email(email)
    if not em or not is_valid_email(em):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(TRIM(email)) = ? ORDER BY rowid DESC LIMIT 1",
            (em,),
        ).fetchone()
    return dict(row) if row else None


def find_user_by_login(login: str) -> dict | None:
    login = (login or "").strip()
    if not login:
        return None
    if "@" in login:
        return find_user_by_email(login)
    return find_user_by_phone(login)


def is_web_account_complete(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("password_hash"):
        return True
    completed = (user.get("web_account_completed_at") or "").strip()
    return bool(completed)


def is_synthetic_web_user(telegram_id: int) -> bool:
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return False
    return tid <= WEB_SYNTHETIC_ID_FLOOR


def allocate_synthetic_telegram_id() -> int:
    with _connect() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT value FROM settings WHERE key = 'web_synthetic_id_seq'"
        ).fetchone()
        current = int(row[0]) if row and row[0] is not None else WEB_SYNTHETIC_ID_FLOOR
        next_id = current - 1
        if next_id >= 0:
            next_id = WEB_SYNTHETIC_ID_FLOOR
        cur.execute(
            """
            INSERT INTO settings (key, value) VALUES ('web_synthetic_id_seq', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(next_id),),
        )
        conn.commit()
        return next_id


def hash_password(password: str) -> str:
    import bcrypt

    pwd = (password or "").encode("utf-8")
    return bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    import bcrypt

    try:
        return bcrypt.checkpw((password or "").encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _hash_otp(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest()


def create_auth_challenge(
    *,
    purpose: str,
    phone: str | None = None,
    email: str | None = None,
    user_telegram_id: int | None = None,
    payload: dict | None = None,
    code: str | None = None,
) -> tuple[str, str]:
    challenge_id = str(uuid.uuid4())
    otp = (code or "").strip() or f"{secrets.randbelow(9000) + 1000:04d}"
    expires_at = int(time.time()) + CHALLENGE_TTL_SEC
    phone_n = normalize_lookup_phone(phone or "") or None
    email_n = normalize_email(email or "") or None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO web_auth_challenges (
                id, phone_number, email, purpose, code_hash, user_telegram_id,
                payload_json, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                challenge_id,
                phone_n,
                email_n,
                purpose,
                _hash_otp(otp),
                user_telegram_id,
                json.dumps(payload or {}, ensure_ascii=False),
                expires_at,
            ),
        )
        conn.commit()
    return challenge_id, otp


def verify_auth_challenge(challenge_id: str, code: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM web_auth_challenges WHERE id = ?",
            (challenge_id,),
        ).fetchone()
        if not row:
            return None
        rec = dict(row)
        if int(rec.get("expires_at") or 0) < int(time.time()):
            conn.execute("DELETE FROM web_auth_challenges WHERE id = ?", (challenge_id,))
            conn.commit()
            return None
        if rec.get("code_hash") != _hash_otp(code):
            return None
        conn.execute("DELETE FROM web_auth_challenges WHERE id = ?", (challenge_id,))
        conn.commit()
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(rec.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        rec["payload"] = payload
        return rec


def set_user_password(telegram_id: int, password: str) -> bool:
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return False
    if len(password or "") < 6:
        return False
    ph = hash_password(password)
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET password_hash = ?, web_account_completed_at = datetime('now')
            WHERE telegram_id = ?
            """,
            (ph, tid),
        )
        conn.commit()
        return cur.rowcount > 0


def save_web_only_user(
    *,
    full_name: str,
    last_name: str,
    email: str,
    address: str,
    phone_number: str,
    display_name: str,
) -> int:
    uid = allocate_synthetic_telegram_id()
    save_user(
        user_id=uid,
        full_name=full_name.strip(),
        last_name=last_name.strip(),
        email=normalize_email(email),
        address=address.strip(),
        phone_number=normalize_lookup_phone(phone_number),
        display_name=display_name.strip(),
        username=None,
    )
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET auth_source = 'web', channel_rules_ack = 1 WHERE telegram_id = ?",
            (uid,),
        )
        conn.commit()
    return uid


def user_public_profile(user: dict) -> dict:
    tid = int(user.get("telegram_id") or 0)
    dn = (user.get("display_name") or "").strip()
    fn = f"{user.get('full_name') or ''} {user.get('last_name') or ''}".strip()
    return {
        "telegram_id": tid,
        "display_name": dn or fn or "کاربر",
        "full_name": user.get("full_name"),
        "last_name": user.get("last_name"),
        "email": _mask_email(user.get("email")),
        "phone_number": _mask_phone(user.get("phone_number")),
        "has_telegram": tid > 0,
        "is_web_only": is_synthetic_web_user(tid),
        "web_account_complete": is_web_account_complete(user),
        "auth_source": user.get("auth_source") or "telegram",
        "is_admin": tid in set(ADMIN_IDS or []),
    }


def user_self_profile(user: dict) -> dict:
    """Full profile for authenticated user (own account page)."""
    tid = int(user.get("telegram_id") or 0)
    dn = (user.get("display_name") or "").strip()
    fn = f"{user.get('full_name') or ''} {user.get('last_name') or ''}".strip()
    from config.settings import BOT_USERNAME, CHANNEL_USERNAME

    bot_user = (BOT_USERNAME or "Sepid_Group_Bot").strip().lstrip("@")
    ch_user = (CHANNEL_USERNAME or "Sepid_Exchange").strip().lstrip("@")
    return {
        "telegram_id": tid,
        "display_name": dn or fn or "کاربر",
        "full_name": user.get("full_name"),
        "last_name": user.get("last_name"),
        "username": user.get("username"),
        "email": user.get("email"),
        "phone_number": user.get("phone_number"),
        "address": user.get("address"),
        "has_telegram": tid > 0,
        "is_web_only": is_synthetic_web_user(tid),
        "web_account_complete": is_web_account_complete(user),
        "auth_source": user.get("auth_source") or "telegram",
        "is_admin": tid in set(ADMIN_IDS or []),
        "has_password": bool(user.get("password_hash")),
        "bot_link": f"https://t.me/{bot_user}",
        "channel_link": f"https://t.me/{ch_user}",
        "can_publish_adverts": tid > 0,
    }


def _mask_email(email: str | None) -> str | None:
    em = (email or "").strip()
    if not em or "@" not in em:
        return em or None
    local, domain = em.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"


def _mask_phone(phone: str | None) -> str | None:
    p = (phone or "").strip()
    if len(p) <= 6:
        return p or None
    return p[:4] + "****" + p[-3:]


def validate_display_name(name: str) -> str | None:
    dn = (name or "").strip()
    if len(dn) < 2:
        return "نام نمایشی باید حداقل ۲ کاراکتر باشد."
    if len(dn) > 40:
        return "نام نمایشی حداکثر ۴۰ کاراکتر."
    if display_name_exists(dn):
        return "این نام نمایشی قبلاً ثبت شده است."
    return None


def list_public_euro_adverts(*, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    lim = max(1, min(int(limit), 50))
    off = max(0, int(offset))
    with _connect() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) FROM euro_adverts
            WHERE COALESCE(status, 'فعال') = 'فعال'
              AND channel_message_id IS NOT NULL
            """
        ).fetchone()
        total = int(total_row[0] or 0) if total_row else 0
        rows = conn.execute(
            """
            SELECT
                a.rowid AS rowid,
                a.user_id,
                a.full_name,
                COALESCE(u.display_name, a.full_name) AS owner_name,
                a.operation,
                a.euro_amount,
                a.rate_toman,
                a.description,
                a.methods,
                a.account_country,
                a.instant_transfer,
                a.city_ir,
                a.city_int,
                a.fee_override_eur,
                COALESCE(a.euro_exchange, 0) AS euro_exchange,
                a.status,
                a.created_at,
                a.channel_message_id,
                a.channel_chat_id
            FROM euro_adverts a
            LEFT JOIN users u ON u.telegram_id = a.user_id
            WHERE COALESCE(a.status, 'فعال') = 'فعال'
              AND a.channel_message_id IS NOT NULL
            ORDER BY a.rowid DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()
    return [dict(r) for r in rows], total
