# Sepid Exchange — Web Companion

مکمل وب ربات [@Sepid_Group_Bot](https://t.me/Sepid_Group_Bot) — **همان دیتابیس SQLite** و قوانین business ربات.

ریپو: [Sepid_Exchange_Wesite](https://github.com/soha15167/Sepid_Exchange_Wesite) · مستندات کامل: [README.md](../README.md)

---

## پورت‌ها و سرویس‌ها

| سرویس | پورت | systemd | مسیر پیشنهادی |
|--------|------|---------|----------------|
| Web API (FastAPI) | **8100** | `sepid-web-api` | `/root/telegram_bot_project2` |
| Web UI (Next.js) | **3100** | `sepid-web-ui` | `/root/web` |
| Telegram bot | — | `telegram-bot` | `/root/telegram_bot_project2` |

API **باید کنار ربات** بماند (import از `handlers/`، `database/`، `services/`).  
UI می‌تواند در `/root/web` جدا deploy شود.

---

## قابلیت‌های وب (فعلی)

| بخش | وب | ربات |
|-----|-----|------|
| ثبت‌نام / OTP / ورود | ✅ | ✅ |
| اتصال کاربر تلگرام (link-password) | ✅ | ✅ |
| ثبت آگهی یورو / معاوضه | ✅ | ✅ |
| عضویت کانال قبل از publish | ✅ | ✅ |
| پیشنهاد روی آگهی | ✅ | ✅ |
| پذیرش/رد پیشنهاد | ✅ | ✅ |
| مذاکره (خواندن transcript) | ✅ read | ✅ chat |
| Deal Gate: تأیید نهایی + حساب | ✅ | ✅ |
| Deal Gate: رسید و پرداخت ادمین | ❌ | ✅ |
| پنل ادمین | ✅ (اکثر منو) | ✅ |
| VPN / سرویس‌های دیگر | ❌ | ✅ |

---

## نصب محلی (ویندوز)

```powershell
cd telegram_bot_project2
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-web.txt
copy .env.sepid.example .env
# ویرایش .env

python -c "from database.db import ensure_schema; ensure_schema()"
python scripts/run_web_api.py

cd web
npm install
npm run dev
```

- API: http://127.0.0.1:8100/api/health  
- UI: http://127.0.0.1:3100  
- Swagger: http://127.0.0.1:8100/docs  

---

## نصب سرور

```bash
# API
cd /root/telegram_bot_project2
source venv/bin/activate
pip install -r requirements-web.txt
cp deploy/sepid-web-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sepid-web-api

# UI
mkdir -p /root/web
# scp -r web/* root@server:/root/web/
cd /root/web && npm install && npm run build
cp /root/telegram_bot_project2/deploy/sepid-web-ui.service /etc/systemd/system/
systemctl enable --now sepid-web-ui
```

اسکript کمکی: `scripts/server_start_web.sh`

### HTTPS (nginx)

```bash
cp deploy/nginx-sepid.conf /etc/nginx/sites-available/sepid
# server_name را عوض کنید
nginx -t && systemctl reload nginx
certbot --nginx -d your-domain.example
```

جزئیات: [deploy/README.md](../deploy/README.md)

---

## env

فایل `.env` **یکی** در `/root/telegram_bot_project2` برای ربات + API.  
نمونهٔ متغیرهای وب: [.env.web.example](../.env.web.example)

| متغیر | نقش |
|--------|-----|
| `WEB_JWT_SECRET` | امضای JWT |
| `WEB_DEV_OTP_IN_RESPONSE` | فقط dev — OTP در JSON |
| `WEB_FRONTEND_URL` | CORS |
| `BOT_TOKEN` | انتشار کانال، deal gate، notify |
| `ADVERT_CHANNEL_ID` | publish + membership |
| `TWILIO_*` | OTP production |

---

## API خلاصه

| مسیر | توضیح |
|------|--------|
| `POST /api/auth/*` | lookup, OTP, register, login |
| `GET/PATCH /api/auth/me` | پروفایل |
| `GET /api/adverts` | لیست عمومی |
| `POST /api/adverts` | ثبت (عضویت کانال) |
| `POST /api/adverts/{id}/offers` | پیشنهاد |
| `POST /api/offers/{id}/accept` | پذیرش → deal gate |
| `GET /api/deals/{id}` | وضعیت gate |
| `POST /api/deals/{id}/response` | بله/خیر |
| `POST /api/deals/{id}/accounts` | متن حساب |
| `GET /api/offers/{id}/negotiation` | transcript مذاکره |
| `GET /api/admin/*` | پنل ادمین |

---

## کاربران

1. **کاربر ربات:** lookup → OTP → link-password → ورود با رمز  
2. **فقط وب:** OTP → register (telegram_id منفی) — **ثبت آگهی نیاز به اتصال تلگرام**  
3. **محدودیت:** کاربر restricted در API هم مسدود می‌شود  

---

## SCP (به‌روزرسانی)

**Backend:**

```text
scp "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\web_api\deps.py" "root@49.13.132.230:/root/telegram_bot_project2/web_api/"
scp "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\web_api\routers\auth.py" "root@49.13.132.230:/root/telegram_bot_project2/web_api/routers/"
scp "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\web_api\routers\offers.py" "root@49.13.132.230:/root/telegram_bot_project2/web_api/routers/"
scp "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\database\web_auth.py" "root@49.13.132.230:/root/telegram_bot_project2/database/"
scp "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\services\deal_gate_web.py" "root@49.13.132.230:/root/telegram_bot_project2/services/"
```

**Frontend → `/root/web/`:**

```text
scp -r "C:\Users\Sohei\Desktop\Desktop\telegram_bot_project2\web\src" "root@49.13.132.230:/root/web/"
```

بعد:

```bash
systemctl restart sepid-web-api
cd /root/web && npm run build && systemctl restart sepid-web-ui
```

---

## یادداشت

- `ensure_schema()` با restart API/bot اجرا می‌شود.  
- رفتار ربات با اضافه شدن وب **عوض نمی‌شود** مگر endpoint مشترک صدا زده شود.  
- رسید و تسویه ادمین: فعلاً فقط ربات — در UI پیام «ادامه از تلگرام» نمایش داده می‌شود.
