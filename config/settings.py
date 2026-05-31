"""
config/settings.py — Environment configuration / تنظیمات محیط

EN: Loads variables from `.env` (never commit secrets). Used project-wide.
FA: خواندن `.env` — توکن ربات، کانال، دیتابیس، Twilio، ادمین.
"""

from dotenv import load_dotenv
import json
import os
import re

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
# نام نمایشی داخل پیامک Verify (جای «My New Verify Service») — مثلاً Sepid Group
# بدون فاصله یا داخل کوتیشن در .env: "Sepid Group"
TWILIO_VERIFY_FRIENDLY_NAME = (os.getenv("TWILIO_VERIFY_FRIENDLY_NAME") or "Sepid_Group").strip()
# زبان قالب Verify در Console — fa برای فارسی؛ خالی = بر اساس کشور شماره
TWILIO_VERIFY_LOCALE = (os.getenv("TWILIO_VERIFY_LOCALE") or "").strip()
# متن فارسی SMS_OTP_BODY_TEMPLATE — پیش‌فرض: روشن اگر TWILIO_FROM_PHONE_NUMBER پر باشد
_otp_custom_flag = (os.getenv("TWILIO_OTP_USE_CUSTOM_TEMPLATE") or "").strip().lower()
if _otp_custom_flag in ("0", "false", "no"):
    TWILIO_OTP_USE_CUSTOM_TEMPLATE = False
elif _otp_custom_flag in ("1", "true", "yes"):
    TWILIO_OTP_USE_CUSTOM_TEMPLATE = True
else:
    TWILIO_OTP_USE_CUSTOM_TEMPLATE = bool((TWILIO_FROM or "").strip())
# متن پیامک کلاسیک (بدون Verify) — {code} = کد چهاررقمی
SMS_OTP_BODY_TEMPLATE = (
    os.getenv("SMS_OTP_BODY_TEMPLATE")
    or "به کانال Sepid_Exchange خوش آمدید.\nکد تأیید ثبت‌نام شما: {code}"
).strip()

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
def _env_int_id_list(*keys: str) -> list[int]:
    """چند آیدی از env (جداشده با ویرگول/فاصله/سمی‌کالن) — کاربر یا گروه."""
    out: list[int] = []
    seen: set[int] = set()
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        for part in re.split(r"[,;\s]+", raw):
            part = part.strip()
            if not part:
                continue
            try:
                n = int(part)
            except ValueError:
                continue
            if n == 0 or n in seen:
                continue
            seen.add(n)
            out.append(n)
    return out


ADMIN_IDS = _env_int_id_list("ADMIN_USER_ID")
# اختیاری: گروه/چت اضافه برای اعلان معامله (مثلاً گروه ادمین‌ها)
ADMIN_NOTIFY_CHAT_IDS = _env_int_id_list(
    "DEAL_ADMIN_NOTIFY_CHAT_ID", "ADMIN_NOTIFY_CHAT_ID"
)

# --- Bank cards (admin quick send) / کارت‌های بانکی ---
# JSON array, example in .env.sepid.example
_bank_cards_raw = (os.getenv("BANK_CARDS_JSON") or "").strip()
if _bank_cards_raw:
    try:
        BANK_CARDS = json.loads(_bank_cards_raw)
        if not isinstance(BANK_CARDS, list):
            BANK_CARDS = []
    except Exception:
        BANK_CARDS = []
else:
    BANK_CARDS = []

# --- Iran ledger panel (transactions API) / سایت مدیریت ورودی/خروجی ایران ---
# Example: http://49.13.132.230:8000
IRAN_PANEL_BASE_URL = (os.getenv("IRAN_PANEL_BASE_URL") or "http://49.13.132.230:8000").strip().rstrip("/")

# --- Receipt vision (AI) for /txin /txout — optional, replaces weak Tesseract when set ---
RECEIPT_VISION_API_KEY = (
    os.getenv("RECEIPT_VISION_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
).strip()
# gpt-4o-mini ارزان؛ برای فیش‌های سخت (Blu تیره) gpt-4o دقیق‌تر (~۳–۵× هزینه)
RECEIPT_VISION_MODEL = (os.getenv("RECEIPT_VISION_MODEL") or "gpt-4o-mini").strip()
RECEIPT_VISION_BASE_URL = (
    os.getenv("RECEIPT_VISION_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
).strip().rstrip("/")
try:
    RECEIPT_VISION_TIMEOUT_SEC = float(os.getenv("RECEIPT_VISION_TIMEOUT_SEC") or "0")
except ValueError:
    RECEIPT_VISION_TIMEOUT_SEC = 0.0
if RECEIPT_VISION_TIMEOUT_SEC <= 0:
    _rv_host = RECEIPT_VISION_BASE_URL.lower()
    # Ollama روی CPU: بیش از ~۱۰۰ث معمولاً timeout UX بد است؛ OCR سریع baam جبران می‌کند
    RECEIPT_VISION_TIMEOUT_SEC = (
        100.0 if ("11434" in _rv_host or "ollama" in _rv_host) else 90.0
    )
_rv_raw = (os.getenv("RECEIPT_VISION_ENABLED") or "").strip().lower()
if _rv_raw in ("0", "false", "no", "off"):
    RECEIPT_VISION_ENABLED = False
elif _rv_raw in ("1", "true", "yes", "on"):
    RECEIPT_VISION_ENABLED = True
else:
    RECEIPT_VISION_ENABLED = bool(RECEIPT_VISION_API_KEY)

# Ollama روی VPS کم‌رم: پیش‌فرض Vision خاموش — فقط OCR سریع baam (۱=فعال‌سازی Vision محلی)
_rv_ollama_raw = (os.getenv("RECEIPT_VISION_USE_OLLAMA") or "").strip().lower()
RECEIPT_VISION_USE_OLLAMA = _rv_ollama_raw in ("1", "true", "yes", "on")

# Recent list cap (users, adverts, offers) / سقف نمایش «آخرین» در لیست‌ها
LIST_RECENT_LIMIT = 10

_DAILY_REPORT_RAW = (os.getenv("DAILY_REPORT_ENABLED") or "1").strip().lower()
DAILY_REPORT_ENABLED = _DAILY_REPORT_RAW not in ("0", "false", "no", "off")
try:
    DAILY_REPORT_HOUR = int((os.getenv("DAILY_REPORT_HOUR") or "9").strip())
except ValueError:
    DAILY_REPORT_HOUR = 9
try:
    DAILY_REPORT_MINUTE = int((os.getenv("DAILY_REPORT_MINUTE") or "0").strip())
except ValueError:
    DAILY_REPORT_MINUTE = 0

_BROADCAST_ON_ENABLE_RAW = (os.getenv("BROADCAST_ON_ENABLE") or "1").strip().lower()
BROADCAST_ON_ENABLE = _BROADCAST_ON_ENABLE_RAW not in ("0", "false", "no", "off")

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
