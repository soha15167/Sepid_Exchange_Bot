# 📁 فایل config/settings.py (نسخه اصلاح‌شده با توجه به .env جدید)

from dotenv import load_dotenv
import os

# بارگذاری متغیرهای محیطی
load_dotenv()

# 🔐 اطلاعات ربات و Twilio
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
CHANNEL_USERNAME = (os.getenv("CHANNEL_USERNAME") or "Sepid_Exchange").strip().lstrip("@")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM_PHONE_NUMBER")

# 📢 اطلاعات مدیریتی و پایگاه داده
ADVERT_CHANNEL_ID = os.getenv("ADVERT_CHANNEL_ID")
DB_PATH = os.getenv("DATABASE_NAME", default="eurobot.db")
USER_DATA_JSON = "user_data.json"

# شمارهٔ نمایشی آگهی بعد از ریست دیتابیس (اولین آگهی = همین عدد)
try:
    ADVERT_ID_START = int((os.getenv("ADVERT_ID_START") or "3196").strip())
except ValueError:
    ADVERT_ID_START = 3196

# 🛡️ لیست ادمین‌ها از یک مقدار ثابت (تبدیل به int)
ADMIN_IDS = [int(os.getenv("ADMIN_USER_ID", "0"))]

# اختیاری: اجرا از پنل ادمین (دکمهٔ ری‌استارت) — فقط مقدار ثابت از .env، ورودی کاربر نیست
BOT_RESTART_COMMAND = (os.getenv("BOT_RESTART_COMMAND") or "").strip()

# قبل از ری‌استارت سرور: به کاربران ثبت‌نام‌شده (به‌جز ادمین‌ها) یک منوی اصلی تازه می‌رود. برای خاموش کردن کامل: 0
_RESTART_BROADCAST_RAW = (os.getenv("BOT_RESTART_BROADCAST_MENU") or "1").strip().lower()
BOT_RESTART_BROADCAST_MENU = _RESTART_BROADCAST_RAW not in ("0", "false", "no", "off")

# بعد از تأیید پیشنهاد: راهنمای فوروارد به ادمین (مثلاً @Sepid_Group_Admin)
DEAL_NEXT_STEPS_ADMIN = (os.getenv("DEAL_NEXT_STEPS_ADMIN") or "").strip()

# 🧪 بررسی وجود توکن
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN در فایل .env تعریف نشده است!")
