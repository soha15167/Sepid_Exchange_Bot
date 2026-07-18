from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import ADMIN_IDS
from database.db import get_restriction_block_message, get_user
from database.web_auth import is_web_account_complete
from web_api.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ورود لازم است.")
    payload = decode_access_token(creds.credentials)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="نشست نامعتبر است.")
    try:
        uid = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="نشست نامعتبر است.")
    user = get_user(uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="کاربر یافت نشد.")
    if not is_web_account_complete(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="تکمیل حساب وب لازم است.")
    block = get_restriction_block_message(uid)
    if block:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=block)
    user["_is_admin"] = uid in set(ADMIN_IDS or [])
    return user


def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    if not creds or not creds.credentials:
        return None
    payload = decode_access_token(creds.credentials)
    if not payload or not payload.get("sub"):
        return None
    try:
        uid = int(payload["sub"])
    except (TypeError, ValueError):
        return None
    user = get_user(uid)
    if not user or not is_web_account_complete(user):
        return None
    user["_is_admin"] = uid in set(ADMIN_IDS or [])
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("_is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="دسترسی ادمین لازم است.")
    return user
