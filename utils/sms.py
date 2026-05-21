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
    TWILIO_FROM,
    TWILIO_SID,
    TWILIO_TOKEN,
    TWILIO_VERIFY_SERVICE_SID,
)

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

_OTP_BODY = "کد تایید شما برای کانال Sepid_Exchange: {code}"


def uses_twilio_verify() -> bool:
    return bool((TWILIO_VERIFY_SERVICE_SID or "").strip())


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


def _send_via_twilio_verify(to_number: str) -> bool:
    service_sid = (TWILIO_VERIFY_SERVICE_SID or "").strip()
    client = _twilio_client()
    if not client or not service_sid:
        return False
    try:
        verification = client.verify.v2.services(service_sid).verifications.create(
            to=_e164(to_number),
            channel="sms",
        )
        return (verification.status or "").lower() in ("pending", "approved")
    except Exception as e:
        print(f"❌ Twilio Verify send: {e}")
        return False


def _send_via_legacy_sms(to_number: str, code: str) -> bool:
    if not TWILIO_FROM:
        return False
    client = _twilio_client()
    if not client:
        return False
    try:
        client.messages.create(
            body="\u200F" + _OTP_BODY.format(code=code),
            from_=TWILIO_FROM,
            to=_e164(to_number),
        )
        return True
    except Exception as e:
        print(f"❌ SMS legacy: {e}")
        return False


def send_verification_sms(to_number: str, code: str) -> bool:
    """ارسال OTP — با Verify کد را Twilio می‌سازد (code نادیده گرفته می‌شود)."""
    if uses_twilio_verify():
        return _send_via_twilio_verify(to_number)
    return _send_via_legacy_sms(to_number, code)


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
    msg = MIMEText(_OTP_BODY.format(code=code), "plain", "utf-8")
    msg["Subject"] = "کد تأیید Sepid Exchange"
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
    if user_data.get("otp_verify_twilio") and uses_twilio_verify():
        return check_verification_sms(phone, entered)
    return entered == str(user_data.get("sms_code") or "").strip()
