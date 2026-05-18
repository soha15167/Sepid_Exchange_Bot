"""
utils/validators.py — Input validation / اعتبارسنجی ورودی

EN: Email and international phone format checks for registration.
FA: بررسی فرمت ایمیل و شماره موبایل در ثبت‌نام.
"""

import re

def is_valid_email(email):
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    return re.match(pattern, email) is not None

def is_valid_phone(phone):
    pattern = r"^\+[1-9]\d{7,14}$"  # پشتیبانی از فرمت بین‌المللی
    return re.match(pattern, phone) is not None


def normalize_phone_input(raw: str) -> str:
    """
    یکدست‌سازی ورودی شماره قبل از اعتبارسنجی:
    حذف فاصله/خط تیره، ارقام فارسی/عربی، ۰۹۱۲… و ۹۱۲… ایران → +98…
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"[\s\-\u200f\u200e().]", "", s)
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if re.fullmatch(r"09\d{9}", s):
            s = "+98" + s[1:]
        elif re.fullmatch(r"9\d{9}", s):
            s = "+98" + s
        elif re.fullmatch(r"989\d{9}", s):
            s = "+" + s
        elif s.startswith("0") and len(s) >= 10 and s[1] == "9":
            s = "+98" + s[1:]
    return s
