# Sepid Exchange Bot

ربات تلگرام کانال [**@Sepid_Exchange**](https://t.me/Sepid_Exchange) برای ثبت‌نام کاربران، انتشار آگهی خرید/فروش و معاوضهٔ یورو، و مدیریت پیشنهادها روی پست‌های کانال.

ربات رسمی: [@Sepid_Group_Bot](https://t.me/Sepid_Group_Bot)

## قابلیت‌ها

- ثبت‌نام کاربر با تأیید SMS (Twilio) و نام نمایشی یکتا در آگهی‌ها
- ثبت آگهی خرید/فروش یورو (نرخ تومان) و معاوضه Euro به Euro
- انتشار خودکار آگهی در کانال با دکمه «پیشنهاد به آگهی»
- فلو پیشنهاد (موافقت با آگهی / پیشنهاد با مقدار و نرخ جدید)
- پنل ادمین: کاربران، آگهی‌ها، پیشنهادها، محدودیت، کارمزد
- قوانین و روال کار کانال در منوی ربات

## پیش‌نیازها

- Python 3.10+
- توکن ربات از [@BotFather](https://t.me/BotFather)
- ربات **ادمین کانال** با حق ارسال پیام
- حساب Twilio برای کد تأیید موبایل (ثبت‌نام)

## نصب محلی

```bash
git clone https://github.com/soha15167/Sepid_Exchange_Bot.git
cd Sepid_Exchange_Bot

python -m venv venv
# Windows: venv\Scripts\activate
# Linux:   source venv/bin/activate

pip install -r requirements.txt
```

### تنظیم محیط

فایل `.env` را از نمونه بسازید و مقادیر را پر کنید:

```bash
cp .env.sepid.example .env
```

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن ربات |
| `BOT_USERNAME` | یوزرنیم ربات (بدون @) |
| `CHANNEL_USERNAME` | یوزرنیم کانال، مثلاً `Sepid_Exchange` |
| `ADVERT_CHANNEL_ID` | شناسهٔ عددی کانال (معمولاً `-100…`) |
| `ADMIN_USER_ID` | آیدی عددی تلگرام ادمین |
| `DATABASE_NAME` | مسیر فایل SQLite (پیش‌فرض `eurobot.db`) |
| `ADVERT_ID_START` | شمارهٔ اولین آگهی پس از دیتابیس تازه (مثلاً `3196`) |
| `TWILIO_*` | تنظیمات SMS |
| `DEAL_NEXT_STEPS_ADMIN` | یوزرنیم ادمین معاملات (بدون @) |

### دیتابیس تازه

برای شروع از صفر (ثبت‌نام مجدد همهٔ کاربران):

```bash
python scripts/init_fresh_database.py
```

شمارندهٔ آگهی طبق `ADVERT_ID_START` تنظیم می‌شود.

### اجرا

```bash
python main.py
```

## مستندات کد / Code documentation

راهنمای دو‌زبانه (فارسی + English) برای هر بخش:

- **[docs/CODE_OVERVIEW.md](docs/CODE_OVERVIEW.md)** — معماری، نقشهٔ فایل‌ها، فلوها
- Docstrings at the top of each `.py` module — توضیح کوتاه در ابتدای هر فایل

## ساختار پروژه

```
├── main.py              # نقطهٔ ورود و ثبت هندلرها
├── config/settings.py   # تنظیمات از .env
├── database/db.py       # SQLite و migration سبک
├── handlers/            # فلوهای ربات (آگهی، پیشنهاد، ادمین، …)
├── keyboards/           # منوها و دکمه‌های اینلاین
├── utils/               # فرمت کانال، SMS، تلگرام
└── scripts/             # ابزارهای نگهداری (دیتابیس تازه)
```

## نرخ روزانه Bonbast در کانال

هر روز **ساعت ۱۲:۰۰ به وقت تهران** (قابل تغییر در `.env`) نرخ ارزهای انتخاب‌شده از [bonbast.com](https://www.bonbast.com) در کانال منتشر می‌شود.

| محیط | فایل نمونه |
|------|------------|
| کانال/ربات قبلی (تست) | `.env.legacy.example` |
| Sepid جدید | `.env.sepid.example` |

```env
BONBAST_DAILY_POST_ENABLED=1
BONBAST_DAILY_HOUR=12
BONBAST_DAILY_MINUTE=0
BONBAST_CURRENCY_CODES=usd,eur,gbp,aed,try,chf,cad,sek
# اختیاری — اگر آگهی‌ها کانال دیگری دارند ولی نرخ در کانال قدیمی می‌ماند:
# BONBAST_CHANNEL_ID=-100...
```

پیش‌فرض: `BONBAST_CHANNEL_ID` خالی → همان `ADVERT_CHANNEL_ID`.

تست فوری (فقط ادمین): `/post_rates` در چت خصوصی با ربات.

نیاز: `pip install -r requirements.txt` (شامل `job-queue`).

## دیپلوی روی سرور

1. کد را روی سرور قرار دهید (مثلاً `/root/telegram_bot_project2`).
2. `.env` و `eurobot.db` را **فقط روی سرور** نگه دارید (در گیت نیستند).
3. پس از تغییر کد، فایل‌های لازم را با `scp` منتقل و سرویس ربات را ری‌استارت کنید.

**مهم:** اگر `systemctl` از `venv/bin/python3` استفاده می‌کند، وابستگی‌ها را **داخل venv** نصب کنید (نه فقط `pip` سراسری):

```bash
cd /root/telegram_bot_project2
./venv/bin/python3 -m pip install -r requirements.txt
./venv/bin/python3 -c "from telegram.ext import JobQueue; JobQueue(); print('job-queue ok')"
systemctl restart telegram-bot
```

## امنیت

- هرگز `.env` یا فایل `*.db` را commit نکنید.
- توکن ربات و کلید Twilio را فقط در محیط سرور نگه دارید.

## لایسنس

پروژهٔ خصوصی کانال Sepid Exchange — استفادهٔ عمومی بدون اجازه مجاز نیست.
