from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from config.settings import WEB_JWT_EXPIRE_HOURS, WEB_JWT_SECRET

ALGORITHM = "HS256"


def get_jwt_secret() -> str:
    secret = (WEB_JWT_SECRET or "").strip()
    if secret:
        return secret
    return "dev-insecure-change-me-" + secrets.token_hex(16)


def create_access_token(*, telegram_id: int, is_admin: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=max(1, WEB_JWT_EXPIRE_HOURS))
    payload: dict[str, Any] = {
        "sub": str(int(telegram_id)),
        "exp": expire,
        "adm": bool(is_admin),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        data = jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])
        return data
    except JWTError:
        return None
