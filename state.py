"""
state.py — In-memory session store / حافظهٔ موقت نشست

EN:
  `user_data_store` holds per-telegram-id drafts during multi-step flows
  (selected payment methods, operation buy/sell, cleanup message IDs).
  Not persisted — lost on bot restart. Authoritative user profile is in SQLite.

FA:
  `user_data_store` پیش‌نویس هر کاربر در فلو (روش‌ها، خرید/فروش، پیام‌های
  موقت) است. با ری‌استارت ربات پاک می‌شود. پروفایل واقعی در دیتابیس است.

Note: Canonical UserState enum lives in `models.enums` (used by main.py).
      enum قدیمی این فایل استفاده نمی‌شود — از models.enums استفاده کنید.
"""

from enum import Enum

# telegram_id -> {"methods": [], "operation": "", "main_menu_anchor": {...}, ...}
user_data_store: dict = {}


class UserState(Enum):
    """Legacy enum — prefer models.enums.UserState / enum قدیمی؛ از models.enums استفاده کنید."""

    MAIN_MENU = "MAIN_MENU"
    EURO_AMOUNT = "EURO_AMOUNT"
    EURO_RATE = "EURO_RATE"
    EURO_DESCRIPTION = "EURO_DESCRIPTION"
    EURO_CONFIRM = "EURO_CONFIRM"
    EXCHANGE_INIT = "EXCHANGE_INIT"
    EXCHANGE_AMOUNT = "EXCHANGE_AMOUNT"
    EXCHANGE_CITY_INT = "EXCHANGE_CITY_INT"
    EXCHANGE_CITY_IR = "EXCHANGE_CITY_IR"
    EXCHANGE_DESCRIPTION = "EXCHANGE_DESCRIPTION"
    EXCHANGE_CONFIRM = "EXCHANGE_CONFIRM"
