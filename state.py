# 📁 فایل state.py (ذخیره‌سازی اطلاعات موقت کاربران)

# اطلاعات موقتی کاربر مثل:
# - نوع عملیات (خرید یا فروش)
# - روش‌های پرداخت انتخاب‌شده
# - وضعیت ثبت‌نام مرحله‌ای
user_data_store = {}

# 📘 حالت‌های مختلف کاربر برای فلوها
from enum import Enum

class UserState(Enum):
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
