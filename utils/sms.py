# 📁 فایل utils/sms.py
# ارسال پیامک با Twilio برای تأیید شماره تماس کاربران

import random
from config.settings import TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM

try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

# 📤 تولید کد و ارسال پیامک تأیید
def send_verification_sms(to_number: str, code: str) -> bool:
    if not TWILIO_AVAILABLE:
        print("⚠️ Twilio نصب نشده است.")
        return False

    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        print("⚠️ Twilio در .env کامل نیست (TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM_PHONE_NUMBER).")
        return False

    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(
            body=f"\u200Fکد تایید شما برای کانال Sepid_Exchange: {code}",
            from_=TWILIO_FROM,
            to=to_number
        )
        return True
    except Exception as e:
        print(f"❌ خطا در ارسال پیامک: {e}")
        return False

# 📦 تابع مکمل برای ثبت‌نام

def generate_sms_code() -> str:
    return str(random.randint(1000, 9999))
