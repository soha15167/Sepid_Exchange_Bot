"""
utils/validators.py — Input validation / اعتبارسنجی ورودی

EN: Email and international phone format checks for registration.
FA: بررسی فرمت ایمیل و شماره موبایل در ثبت‌نام.
"""

import re

_RTL = "\u200f"
_LRM = "\u200e"
_RLE = "\u202b"
_PDF = "\u202c"
_LRI = "\u2066"
_PDI = "\u2069"


def is_valid_email(email):
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    return re.match(pattern, email) is not None


def is_valid_phone(phone):
    pattern = r"^\+[1-9]\d{7,14}$"  # پشتیبانی از فرمت بین‌المللی
    return re.match(pattern, phone) is not None


def phone_starts_with_plus(raw: str) -> bool:
    """ورودی باید (پس از حذف فاصله/نشانگر جهت) با + شروع شود."""
    s = (raw or "").strip()
    if not s:
        return False
    s = re.sub(r"^[\u200f\u200e\u202a\u202b\u202c]+", "", s)
    return s.startswith("+")


def _phone_example_line(label: str, number: str) -> str:
    """یک خط مثال — برچسب و شماره هر دو راست‌چین؛ + سمت چپ شماره."""
    phone = f"<code>{_LRM}{_LRI}{number}{_PDI}</code>"
    return f"{_RLE}{_RTL}{label}: {phone}{_PDF}"


def registration_phone_examples_html() -> str:
    """مثال شماره — آلمان قبل از ایران؛ تراز یکسان سمت راست."""
    return (
        f"{_RLE}{_RTL}بطور مثال برای:{_PDF}\n"
        f"{_phone_example_line('کشور آلمان', '+491751234567')}\n"
        f"{_phone_example_line('کشور ایران', '+989121234567')}\n"
        f"{_RLE}{_PDF}\n"
    )


def registration_phone_prompt_html() -> str:
    """راهنمای مرحلهٔ ورود شماره موبایل در ثبت‌نام."""
    return (
        f"{_RTL}📱 شماره موبایل را با <b>+</b> وارد کنید.\n\n"
        f"{_RTL}شماره تماس باید در دسترس باشد چون برای تکمیل ثبت‌نام "
        f"کد تأیید می‌آید و شما باید آن را وارد کنید.\n\n"
        f"{registration_phone_examples_html()}"
    )


def registration_phone_error_html() -> str:
    """پیام خطای شماره — RTL با + در سمت چپ (LRM داخل code)."""
    return (
        f"{_RTL}❌ شماره باید با <b>+</b> شروع شود.\n\n"
        f"{_RTL}شماره تماس باید در دسترس باشد چون برای تکمیل ثبت‌نام "
        f"کد تأیید می‌آید و شما باید آن را وارد کنید.\n\n"
        f"{registration_phone_examples_html()}"
    )


def normalize_phone_input(raw: str) -> str:
    """
    یکدست‌سازی شمارهٔ بین‌المللی (فقط وقتی با + شروع شده):
    حذف فاصله/خط تیره، ارقام فارسی/عربی، 00… → +…
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[\u200f\u200e\u202a\u202b\u202c]+", "", s)
    s = re.sub(r"[\s\-\u200f\u200e().]", "", s)
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if s.startswith("00"):
        s = "+" + s[2:]
    return s
