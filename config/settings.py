"""
config/settings.py — Environment configuration / تنظیمات محیط

EN: Loads variables from `.env` (never commit secrets). Used project-wide.
FA: خواندن `.env` — توکن ربات، کانال، دیتابیس، Twilio، ادمین.
"""

from dotenv import load_dotenv
import os

load_dotenv()

# --- Bot & Twilio / ربات و پیامک ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
CHANNEL_USERNAME = (os.getenv("CHANNEL_USERNAME") or "Sepid_Exchange").strip().lstrip("@")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM_PHONE_NUMBER")
# Twilio Verify v2 — ترجیحاً برای OTP ثبت‌نام (مثل Console → Verify)
TWILIO_VERIFY_SERVICE_SID = (os.getenv("TWILIO_VERIFY_SERVICE_SID") or "").strip()

# --- Channel & database / کانال و دیتابیس ---
ADVERT_CHANNEL_ID = os.getenv("ADVERT_CHANNEL_ID")
DB_PATH = os.getenv("DATABASE_NAME", default="eurobot.db")
USER_DATA_JSON = "user_data.json"

# First visible ad number after fresh DB / شمارهٔ اولین آگهی پس از دیتابیس تازه
try:
    ADVERT_ID_START = int((os.getenv("ADVERT_ID_START") or "3196").strip())
except ValueError:
    ADVERT_ID_START = 3196

# --- Admin / ادمین ---
ADMIN_IDS = [int(os.getenv("ADMIN_USER_ID", "0"))]

# Optional bot restart from admin panel / ری‌استارت اختیاری از پنل
BOT_RESTART_COMMAND = (os.getenv("BOT_RESTART_COMMAND") or "").strip()

# Broadcast fresh menu before server restart (0 = off) / منوی تازه قبل از ری‌استارت
_RESTART_BROADCAST_RAW = (os.getenv("BOT_RESTART_BROADCAST_MENU") or "1").strip().lower()
BOT_RESTART_BROADCAST_MENU = _RESTART_BROADCAST_RAW not in ("0", "false", "no", "off")

# Admin username shown after offer accepted / آیدی ادمین پس از تأیید پیشنهاد
DEAL_NEXT_STEPS_ADMIN = (os.getenv("DEAL_NEXT_STEPS_ADMIN") or "").strip()

# --- Bonbast daily channel post / پست روزانه نرخ بن‌بست ---
_BONBAST_DAILY_RAW = (os.getenv("BONBAST_DAILY_POST_ENABLED") or "1").strip().lower()
BONBAST_DAILY_POST_ENABLED = _BONBAST_DAILY_RAW not in ("0", "false", "no", "off")
try:
    BONBAST_DAILY_HOUR = int((os.getenv("BONBAST_DAILY_HOUR") or "12").strip())
except ValueError:
    BONBAST_DAILY_HOUR = 12
try:
    BONBAST_DAILY_MINUTE = int((os.getenv("BONBAST_DAILY_MINUTE") or "0").strip())
except ValueError:
    BONBAST_DAILY_MINUTE = 0
_bonbast_codes_raw = (os.getenv("BONBAST_CURRENCY_CODES") or "usd,eur,gbp,aed,try,chf,cad,sek").strip()
BONBAST_CURRENCY_CODES = [c.strip().lower() for c in _bonbast_codes_raw.split(",") if c.strip()]

# Bonbast post target: defaults to advert channel; set alone when ads move to a new channel first.
_bonbast_ch_raw = (os.getenv("BONBAST_CHANNEL_ID") or "").strip()
BONBAST_CHANNEL_ID = _bonbast_ch_raw or ADVERT_CHANNEL_ID

# --- Validation / اعتبارسنجی ---
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN در فایل .env تعریف نشده است!")
