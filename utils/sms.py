"""
utils/sms.py — Verification codes / کد تأیید ثبت‌نام

EN: Twilio Verify v2 (preferred) or legacy SMS; Telegram OTP on user request only.
FA: Verify API جدید Twilio؛ در غیر این صورت پیامک کلاسیک.
"""

from __future__ import annotations

import os
import random
import smtplib
from email.mime.text import MIMEText

from config.settings import (
    SMS_OTP_BODY_TEMPLATE,
    TWILIO_FROM,
    TWILIO_OTP_USE_CUSTOM_TEMPLATE,
    TWILIO_SID,
    TWILIO_TOKEN,
    TWILIO_VERIFY_FRIENDLY_NAME,
    TWILIO_VERIFY_LOCALE,
    TWILIO_VERIFY_SERVICE_SID,
)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

_RLM = "\u200f"
_RLE = "\u202b"
_PDF = "\u202c"


def format_rtl_text(text: str) -> str:
    """راست‌چین برای پیامک/ایمیل/تلگرام — RLM هر خط + بلوک RLE."""
    raw = (text or "").strip().replace("\r\n", "\n")
    if not raw:
        return _RLM
    lines: list[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            lines.append("")
            continue
        if line[0] not in (_RLM, _RLE, "\u202a"):
            line = _RLM + line
        lines.append(line)
    inner = "\n".join(lines)
    if inner.startswith(_RLE):
        return inner
    return _RLE + inner + _PDF


def otp_message_plain(code: str) -> str:
    return format_rtl_text(SMS_OTP_BODY_TEMPLATE.format(code=code))


def _otp_sms_body(code: str) -> str:
    return otp_message_plain(code)


def uses_twilio_verify() -> bool:
    return bool((TWILIO_VERIFY_SERVICE_SID or "").strip())


def otp_checked_via_twilio_verify() -> bool:
    """کد با API Verify چک شود (نه مقایسه با sms_code محلی)."""
    return uses_twilio_verify() and not TWILIO_OTP_USE_CUSTOM_TEMPLATE


def _twilio_client() -> Client | None:
    if not TWILIO_AVAILABLE or not (TWILIO_SID and TWILIO_TOKEN):
        return None
    return Client(TWILIO_SID, TWILIO_TOKEN)


def _e164(phone: str) -> str:
    p = (phone or "").strip().replace(" ", "")
    if p.startswith("00"):
        p = "+" + p[2:]
    if p.startswith("0") and not p.startswith("+"):
        p = "+98" + p[1:]
    if not p.startswith("+"):
        p = "+" + p
    return p


def generate_sms_code() -> str:
    return str(random.randint(1000, 9999))


def _verify_kwargs_variants(to_number: str) -> list[dict]:
    """ترتیب تلاش: locale از .env (اول) → پیش‌فرض Twilio → custom name (اگر مجاز)."""
    base = {"to": _e164(to_number), "channel": "sms"}
    locale = (TWILIO_VERIFY_LOCALE or "").strip()
    variants: list[dict] = [{**base, "locale": locale}] if locale else [dict(base)]
    if locale:
        variants.append(dict(base))
    friendly = (TWILIO_VERIFY_FRIENDLY_NAME or "").strip()
    use_custom = (os.getenv("TWILIO_VERIFY_USE_CUSTOM_NAME") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if friendly and use_custom:
        variants.append({**base, "custom_friendly_name": friendly[:50]})
        if locale:
            variants.append(
                {**base, "locale": locale, "custom_friendly_name": friendly[:50]}
            )
    seen: list[str] = []
    unique: list[dict] = []
    for v in variants:
        key = repr(sorted(v.items()))
        if key not in seen:
            seen.append(key)
            unique.append(v)
    return unique


def _send_via_twilio_verify(to_number: str) -> bool:
    service_sid = (TWILIO_VERIFY_SERVICE_SID or "").strip()
    client = _twilio_client()
    if not client or not service_sid:
        return False
    last_err: Exception | None = None
    for kwargs in _verify_kwargs_variants(to_number):
        try:
            verification = client.verify.v2.services(service_sid).verifications.create(
                **kwargs
            )
            if (verification.status or "").lower() in ("pending", "approved"):
                return True
        except Exception as e:
            last_err = e
            err_l = str(e).lower()
            if "custom friendly name" in err_l:
                continue
            if "locale" in err_l and "locale" in kwargs:
                continue
    print(
        f"❌ Twilio Verify send to {_e164(to_number)}: {last_err} "
        f"(sid={'ok' if TWILIO_SID else 'missing'}, "
        f"token={'ok' if TWILIO_TOKEN else 'missing'}, "
        f"service={service_sid or 'missing'})"
    )
    return False


def _send_via_legacy_sms(to_number: str, code: str) -> bool:
    if not TWILIO_FROM:
        return False
    client = _twilio_client()
    if not client:
        return False
    try:
        client.messages.create(
            body=_otp_sms_body(code),
            from_=TWILIO_FROM,
            to=_e164(to_number),
        )
        return True
    except Exception as e:
        print(f"❌ SMS legacy: {e}")
        return False


def send_verification_sms(to_number: str, code: str) -> bool:
    """ارسال OTP — legacy فارسی (اولویت با FROM)؛ در شکست، Verify."""
    to = _e164(to_number)
    if TWILIO_OTP_USE_CUSTOM_TEMPLATE and TWILIO_FROM:
        ok = _send_via_legacy_sms(to_number, code)
        print(
            f"{'✅' if ok else '❌'} OTP legacy (custom template) → {to} "
            f"custom_template={TWILIO_OTP_USE_CUSTOM_TEMPLATE}"
        )
        if ok:
            return True
        if uses_twilio_verify():
            ok_v = _send_via_twilio_verify(to_number)
            print(f"{'✅' if ok_v else '❌'} OTP Verify fallback → {to}")
            return ok_v
        return False
    if uses_twilio_verify():
        ok = _send_via_twilio_verify(to_number)
        print(
            f"{'✅' if ok else '❌'} OTP Twilio Verify → {to} "
            f"(برای متن فارسی TWILIO_OTP_USE_CUSTOM_TEMPLATE=1 و FROM پر باشد)"
        )
        if ok:
            return True
        if TWILIO_FROM:
            ok_l = _send_via_legacy_sms(to_number, code)
            print(f"{'✅' if ok_l else '❌'} OTP legacy fallback → {to}")
            return ok_l
        return False
    ok = _send_via_legacy_sms(to_number, code)
    print(f"{'✅' if ok else '❌'} OTP legacy (fallback) → {to}")
    return ok


def check_verification_sms(to_number: str, code: str) -> bool:
    """تأیید کد با Twilio Verify (بعد از ارسال همان مسیر Verify)."""
    service_sid = (TWILIO_VERIFY_SERVICE_SID or "").strip()
    client = _twilio_client()
    if not client or not service_sid:
        return False
    try:
        result = client.verify.v2.services(service_sid).verification_checks.create(
            to=_e164(to_number),
            code=(code or "").strip(),
        )
        return (result.status or "").lower() == "approved"
    except Exception as e:
        print(f"❌ Twilio Verify check: {e}")
        return False


def send_verification_email(to_email: str, code: str) -> bool:
    host = (os.getenv("SMTP_HOST") or "").strip()
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    from_addr = (os.getenv("SMTP_FROM") or user).strip()
    to_addr = (to_email or "").strip()
    if not (host and user and password and from_addr and to_addr):
        return False
    try:
        port = int((os.getenv("SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    msg = MIMEText(otp_message_plain(code), "plain", "utf-8")
    msg["Subject"] = format_rtl_text("کد تأیید Sepid Group")
    msg["From"] = from_addr
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, port, timeout=25) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception as e:
        print(f"❌ Email OTP: {e}")
        return False


def try_send_verification_sms(phone: str, code: str) -> bool:
    return send_verification_sms(phone, code)


def is_otp_code_valid(phone: str, code: str, *, user_data: dict) -> bool:
    """
    تأیید کد: Twilio Verify اگر پیامک با Verify رفته؛
    وگرنه مقایسه با کد محلی (تلگرام / legacy SMS).
    """
    entered = (code or "").strip()
    if not entered:
        return False
    if user_data.get("otp_telegram_sent"):
        return entered == str(user_data.get("sms_code") or "").strip()
    if user_data.get("otp_verify_twilio") and otp_checked_via_twilio_verify():
        return check_verification_sms(phone, entered)
    return entered == str(user_data.get("sms_code") or "").strip()
