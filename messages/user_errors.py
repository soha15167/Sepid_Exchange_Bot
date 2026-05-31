"""
messages/user_errors.py — User-facing error copy / پیام‌های خطای یکدست

EN: Single source for RTL HTML messages shown to users.
FA: متن‌های خطا و وضعیت برای نمایش در تلگرام.
"""

_RTL = "\u200f"

BOT_DISABLED = (
    f"{_RTL}⛔️ ربات موقتاً <b>غیرفعال</b> است.\n\n"
    "ثبت آگهی، ارسال پیشنهاد و سایر خدمات تا اعلام فعال‌سازی مجاز نیست."
)

RATE_LIMIT_GENERIC = (
    f"{_RTL}⏳ درخواست‌های زیاد. لطفاً چند دقیقه بعد دوباره تلاش کنید."
)

RATE_LIMIT_OTP = (
    f"{_RTL}⏳ تعداد درخواست کد تأیید زیاد است. "
    "لطفاً پس از چند دقیقه دوباره تلاش کنید."
)

RATE_LIMIT_OFFER = (
    f"{_RTL}⏳ پیشنهادهای پشت‌سرهم مجاز نیست. "
    "چند دقیقه صبر کنید و دوباره تلاش کنید."
)

NOT_REGISTERED = f"{_RTL}ابتدا ثبت‌نام را تکمیل کنید."

NOT_FOUND_ADVERT = f"{_RTL}آگهی پیدا نشد."

NOT_FOUND_OFFER = f"{_RTL}پیشنهاد پیدا نشد."

GENERIC_ERROR = (
    f"{_RTL}خطایی رخ داد. اگر تکرار شد با پشتیبانی تماس بگیرید."
)
