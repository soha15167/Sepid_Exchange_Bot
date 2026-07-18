from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config.settings import ADMIN_IDS, WEB_DEV_OTP_IN_RESPONSE
from database.db import get_user, update_user_field
from database.web_auth import (
    create_auth_challenge,
    find_user_by_login,
    find_user_by_phone,
    is_web_account_complete,
    normalize_email,
    normalize_lookup_phone,
    save_web_only_user,
    set_user_password,
    user_public_profile,
    validate_display_name,
    verify_auth_challenge,
    verify_password,
    user_self_profile,
)
from utils.sms import is_otp_code_valid, otp_checked_via_twilio_verify, send_verification_sms, try_send_verification_sms
from utils.validators import is_valid_email, is_valid_phone, phone_starts_with_plus
from web_api.deps import get_current_user
from web_api.schemas import LoginRequest, LookupRequest, OtpLoginRequest, OtpSendRequest, OtpVerifyRequest
from web_api.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterAfterOtpRequest(BaseModel):
    challenge_id: str
    otp_code: str = Field(..., min_length=4, max_length=8)
    full_name: str = Field(..., min_length=2, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    display_name: str = Field(..., min_length=2, max_length=40)
    email: str
    address: str = Field(..., min_length=3, max_length=300)
    phone_number: str
    password: str = Field(..., min_length=6, max_length=128)
    accept_terms: bool = False


class LinkPasswordRequest(BaseModel):
    challenge_id: str
    otp_code: str = Field(..., min_length=4, max_length=8)
    password: str = Field(..., min_length=6, max_length=128)


class ProfilePatchRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=40)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=6, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


def _login_kind(login: str) -> str:
    return "email" if "@" in (login or "") else "phone"


@router.post("/lookup")
def auth_lookup(body: LookupRequest):
    login = (body.login or "").strip()
    if login and "@" not in login and not phone_starts_with_plus(login):
        raise HTTPException(
            status_code=400,
            detail="شماره موبایل باید با + شروع شود. مثال: +989121234567",
        )
    user = find_user_by_login(login)
    if not user:
        return {
            "status": "new_user",
            "login_kind": _login_kind(body.login),
            "message": "کاربر جدید — OTP و ثبت‌نام.",
        }
    profile = user_public_profile(user)
    if is_web_account_complete(user):
        return {
            "status": "existing_web_user",
            "profile": profile,
            "message": "با رمز عبور وارد شوید.",
        }
    return {
        "status": "link_telegram_user",
        "profile": profile,
        "message": "حساب تلگرام یافت شد — OTP و انتخاب رمز.",
    }


@router.post("/otp/send")
def auth_otp_send(body: OtpSendRequest):
    login = (body.login or "").strip()
    user = find_user_by_login(login)
    phone = None
    email = None

    if "@" in login:
        email = normalize_email(login)
        if not is_valid_email(email):
            raise HTTPException(status_code=400, detail="ایمیل نامعتبر است.")
        if user:
            phone = user.get("phone_number")
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="برای OTP با ایمیل، ابتدا با شماره موبایل ثبت‌نام کنید.",
            )
    else:
        phone = normalize_lookup_phone(login)
        if not phone_starts_with_plus(login):
            raise HTTPException(
                status_code=400,
                detail="شماره موبایل باید با + شروع شود. مثال: +989121234567",
            )
        if not is_valid_phone(phone):
            raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است (+989...).")

    purpose = (body.purpose or "login").strip()
    if purpose not in ("login", "register", "link"):
        purpose = "login"

    if purpose == "login":
        if not user or not is_web_account_complete(user):
            raise HTTPException(status_code=400, detail="حساب وب فعال یافت نشد — ابتدا ثبت‌نام کنید.")

    challenge_id, otp = create_auth_challenge(
        purpose=purpose,
        phone=phone,
        email=email or (user.get("email") if user else None),
        user_telegram_id=int(user["telegram_id"]) if user else None,
    )

    sent = try_send_verification_sms(phone, otp)
    if not sent:
        sent = send_verification_sms(phone, otp)

    resp = {
        "ok": True,
        "challenge_id": challenge_id,
        "phone_masked": user_public_profile(user or {"phone_number": phone}).get("phone_number"),
        "purpose": purpose,
        "sms_sent": bool(sent),
    }
    if WEB_DEV_OTP_IN_RESPONSE or not sent:
        resp["dev_otp"] = otp
    return resp


@router.post("/otp/verify")
def auth_otp_verify(body: OtpVerifyRequest):
    rec = verify_auth_challenge(body.challenge_id, body.code)
    if not rec:
        raise HTTPException(status_code=400, detail="کد نامعتبر یا منقضی شده.")

    phone = rec.get("phone_number")
    if phone and otp_checked_via_twilio_verify():
        if not is_otp_code_valid(phone, body.code):
            raise HTTPException(status_code=400, detail="کد تأیید نامعتبر است.")

    user = None
    tid = rec.get("user_telegram_id")
    if tid is not None:
        user = get_user(int(tid))
    if not user and phone:
        user = find_user_by_phone(phone)

    return {
        "ok": True,
        "purpose": rec.get("purpose"),
        "profile": user_public_profile(user) if user else None,
        "needs_password": not (user and is_web_account_complete(user)),
        "needs_registration": user is None,
        "needs_link_only": bool(user and not is_web_account_complete(user)),
    }


@router.post("/link-password")
def auth_link_password(body: LinkPasswordRequest):
    """Existing telegram/bot user: OTP verified + set web password."""
    rec = verify_auth_challenge(body.challenge_id, body.otp_code)
    if not rec:
        raise HTTPException(status_code=400, detail="OTP نامعتبر یا منقضی.")

    user = None
    if rec.get("user_telegram_id") is not None:
        user = get_user(int(rec["user_telegram_id"]))
    if not user and rec.get("phone_number"):
        user = find_user_by_phone(rec["phone_number"])
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")
    if is_web_account_complete(user):
        raise HTTPException(status_code=409, detail="حساب وب قبلاً فعال است — وارد شوید.")

    tid = int(user["telegram_id"])
    if not set_user_password(tid, body.password):
        raise HTTPException(status_code=400, detail="رمز نامعتبر است.")
    user = get_user(tid)
    token = create_access_token(telegram_id=tid, is_admin=tid in set(ADMIN_IDS or []))
    return {"ok": True, "access_token": token, "user": user_public_profile(user)}


@router.post("/register-after-otp")
def auth_register_after_otp(body: RegisterAfterOtpRequest):
    rec = verify_auth_challenge(body.challenge_id, body.otp_code)
    if not rec:
        raise HTTPException(status_code=400, detail="OTP نامعتبر یا منقضی.")
    if not body.accept_terms:
        raise HTTPException(status_code=400, detail="پذیرش قوانین الزامی است.")

    phone = normalize_lookup_phone(body.phone_number)
    if not phone_starts_with_plus(body.phone_number):
        raise HTTPException(status_code=400, detail="شماره موبایل باید با + شروع شود.")
    if not is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است.")
    if rec.get("phone_number") and rec.get("phone_number") != phone:
        raise HTTPException(status_code=400, detail="شماره با OTP مطابقت ندارد.")

    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="ایمیل نامعتبر است.")

    dn_err = validate_display_name(body.display_name)
    if dn_err:
        raise HTTPException(status_code=400, detail=dn_err)

    existing = find_user_by_phone(phone)
    if not existing and rec.get("user_telegram_id"):
        existing = get_user(int(rec["user_telegram_id"]))

    if existing and is_web_account_complete(existing):
        raise HTTPException(status_code=409, detail="حساب قبلاً فعال است.")

    if existing:
        tid = int(existing["telegram_id"])
        update_user_field(tid, "email", email)
        update_user_field(tid, "address", body.address.strip())
        if not (existing.get("display_name") or "").strip():
            update_user_field(tid, "display_name", body.display_name.strip())
        if not set_user_password(tid, body.password):
            raise HTTPException(status_code=400, detail="رمز نامعتبر است.")
        user = get_user(tid)
        token = create_access_token(telegram_id=tid, is_admin=tid in set(ADMIN_IDS or []))
        return {"ok": True, "access_token": token, "user": user_public_profile(user), "linked": True}

    uid = save_web_only_user(
        full_name=body.full_name,
        last_name=body.last_name,
        email=email,
        address=body.address,
        phone_number=phone,
        display_name=body.display_name,
    )
    if not set_user_password(uid, body.password):
        raise HTTPException(status_code=400, detail="رمز نامعتبر است.")
    user = get_user(uid)
    token = create_access_token(telegram_id=uid, is_admin=uid in set(ADMIN_IDS or []))
    return {"ok": True, "access_token": token, "user": user_public_profile(user), "linked": False}


@router.post("/login")
def auth_login(body: LoginRequest):
    user = find_user_by_login(body.login)
    if not user or not is_web_account_complete(user):
        raise HTTPException(status_code=401, detail="ورود نامعتبر.")
    if not verify_password(body.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="ورود نامعتبر.")
    tid = int(user["telegram_id"])
    token = create_access_token(telegram_id=tid, is_admin=tid in set(ADMIN_IDS or []))
    return {"ok": True, "access_token": token, "user": user_public_profile(user)}


@router.post("/login-otp")
def auth_login_otp(body: OtpLoginRequest):
    """Returning web user: sign in with SMS code (email login sends SMS to registered phone)."""
    rec = verify_auth_challenge(body.challenge_id, body.otp_code)
    if not rec:
        raise HTTPException(status_code=400, detail="کد نامعتبر یا منقضی شده.")
    if rec.get("purpose") != "login":
        raise HTTPException(status_code=400, detail="چالش ورود نامعتبر است.")

    phone = rec.get("phone_number")
    if phone and otp_checked_via_twilio_verify():
        if not is_otp_code_valid(phone, body.otp_code):
            raise HTTPException(status_code=400, detail="کد تأیید نامعتبر است.")

    user = None
    if rec.get("user_telegram_id") is not None:
        user = get_user(int(rec["user_telegram_id"]))
    if not user and phone:
        user = find_user_by_phone(phone)
    if not user or not is_web_account_complete(user):
        raise HTTPException(status_code=401, detail="ورود نامعتبر.")

    tid = int(user["telegram_id"])
    token = create_access_token(telegram_id=tid, is_admin=tid in set(ADMIN_IDS or []))
    return {"ok": True, "access_token": token, "user": user_public_profile(user)}


@router.get("/me")
def auth_me(user: dict = Depends(get_current_user)):
    return {"user": user_self_profile(user)}


@router.patch("/me")
def auth_me_patch(body: ProfilePatchRequest, user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    if body.display_name is not None:
        dn = body.display_name.strip()
        current_dn = (user.get("display_name") or "").strip()
        if dn != current_dn:
            err = validate_display_name(dn)
            if err:
                raise HTTPException(status_code=400, detail=err)
        if not update_user_field(uid, "display_name", dn):
            raise HTTPException(status_code=400, detail="ذخیره نام نمایشی ناموفق.")
    fresh = get_user(uid)
    if not fresh:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")
    return {"ok": True, "user": user_self_profile(fresh)}


@router.post("/me/password")
def auth_change_password(body: PasswordChangeRequest, user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    full = get_user(uid)
    if not full or not full.get("password_hash"):
        raise HTTPException(status_code=400, detail="رمز عبور برای این حساب تنظیم نشده.")
    if not verify_password(body.current_password, full.get("password_hash")):
        raise HTTPException(status_code=400, detail="رمز فعلی اشتباه است.")
    if not set_user_password(uid, body.new_password):
        raise HTTPException(status_code=400, detail="ذخیره رمز جدید ناموفق.")
    return {"ok": True}
