# Sepid Exchange Bot Maintenance — 2026-07-18

## English

### Purpose

This maintenance release aligns the repository with the tested production Telegram bot, retires the unwanted companion web application, improves operational security, fixes confirmed runtime failures, and introduces verified online SQLite backups.

### Changes

- Preserved the Persian Telegram bot behavior and the production database schema.
- Removed the retired companion web UI/API source, deployment files, web authentication code, and web-only dependencies.
- Kept the separately operated services that the bot still depends on; the Iran transaction panel integration remains available through `IRAN_PANEL_BASE_URL`.
- Added structured log redaction for Telegram tokens, financial identifiers, phone numbers, email addresses, and exception tracebacks.
- Reduced noisy HTTP client logging and restricted runtime log permissions.
- Fixed an admin callback import-shadowing bug that raised `UnboundLocalError` when opening advert offers.
- Treated Telegram's harmless `Message is not modified` response as success only in the duplicate service-menu edit path; other Telegram errors still propagate normally.
- Replaced unsafe live-database file copying with SQLite's online backup API.
- Added backup integrity verification, atomic publication, private permissions, and retention tests.
- Added `/backups/` to `.gitignore` so generated database backups cannot be committed accidentally.

### Repository reconciliation

The maintenance branch records the latest remote `main` history while preserving the newer, tested production tree. This prevents the retired June web stack from being reintroduced into the July production bot.

### Validation

- Production database integrity checks completed successfully before and after each deployment.
- Database table counts were compared before and after changes and remained consistent with live activity.
- The bot stayed active during online backup creation; no restart was required.
- Logging checks found no exposed token-like values in the active secured log.
- Fourteen automated staging tests passed, followed by production regression tests.
- The administrator advert-offer flow and rapid duplicate buy/sell button flow were manually verified in Persian Telegram.
- Required external services remained available, while retired companion-web ports stayed closed.

### Security and data handling

No `.env` file, database, log, generated backup, private key, or production credential is included in this branch. Configuration secrets remain environment-managed.

---

## فارسی

### هدف

این نسخهٔ نگهداری، مخزن GitHub را با نسخهٔ تست‌شدهٔ ربات تلگرام روی سرور هماهنگ می‌کند، وب‌سایت جانبی غیرضروری را حذف می‌کند، امنیت عملیاتی را افزایش می‌دهد، خطاهای واقعی ربات را برطرف می‌کند و بکاپ امن و قابل‌اعتبارسنجی برای SQLite اضافه می‌کند.

### تغییرات

- عملکرد فارسی ربات تلگرام و ساختار دیتابیس اصلی بدون تغییر مخرب حفظ شد.
- سورس رابط کاربری و API وب جانبی، فایل‌های استقرار وب، احراز هویت مخصوص وب و وابستگی‌های مخصوص وب حذف شدند.
- سرویس‌های جداگانه‌ای که ربات همچنان به آن‌ها نیاز دارد حفظ شدند؛ اتصال پنل تراکنش ایران همچنان از طریق `IRAN_PANEL_BASE_URL` فعال است.
- اطلاعات حساس شامل توکن تلگرام، شماره کارت و شبا، شماره تلفن، ایمیل و traceback خطاها در لاگ‌ها به‌صورت خودکار مخفی می‌شوند.
- لاگ‌های اضافی کتابخانه‌های HTTP کاهش یافتند و سطح دسترسی فایل‌های لاگ محدود شد.
- خطای `UnboundLocalError` در بخش مدیریت پیشنهادهای آگهی برطرف شد.
- خطای بی‌خطر `Message is not modified` فقط در حالت کلیک تکراری منوی خرید و فروش نادیده گرفته می‌شود؛ سایر خطاهای تلگرام همچنان گزارش می‌شوند.
- کپی ساده و ناامن دیتابیس زنده با API رسمی بکاپ آنلاین SQLite جایگزین شد.
- بررسی سلامت بکاپ، انتشار اتمیک، دسترسی خصوصی فایل‌ها و تست نگهداری نسخه‌های بکاپ اضافه شد.
- مسیر `/backups/` به `.gitignore` اضافه شد تا دیتابیس بکاپ‌شده تصادفی وارد GitHub نشود.

### هماهنگ‌سازی مخزن

تاریخچهٔ جدید شاخهٔ `main` در شاخهٔ نگهداری ثبت شده است، اما فایل‌های جدیدتر و تست‌شدهٔ سرور حفظ شده‌اند. به این ترتیب وب‌سایت قدیمی ماه ژوئن دوباره وارد نسخهٔ عملیاتی ماه ژوئیه نمی‌شود.

### اعتبارسنجی

- سلامت دیتابیس اصلی قبل و بعد از هر استقرار با موفقیت بررسی شد.
- تعداد رکوردهای جدول‌های اصلی قبل و بعد از تغییرات مقایسه شد و با فعالیت زندهٔ ربات سازگار باقی ماند.
- هنگام ساخت بکاپ آنلاین، ربات فعال ماند و نیازی به Restart نبود.
- در لاگ امن فعال، هیچ مقدار شبیه توکن مشاهده نشد.
- چهارده تست خودکار در محیط آزمایشی و سپس تست‌های مخصوص روی سرور اصلی با موفقیت اجرا شدند.
- مسیر مدیریت پیشنهاد آگهی و کلیک سریع و تکراری روی دکمه‌های خرید و فروش در ربات فارسی به‌صورت دستی تأیید شدند.
- سرویس‌های خارجی موردنیاز فعال ماندند و پورت‌های وب جانبی حذف‌شده بسته باقی ماندند.

### امنیت و اطلاعات

هیچ فایل `.env`، دیتابیس، لاگ، بکاپ تولیدشده، کلید خصوصی یا اطلاعات ورود سرور در این شاخه قرار ندارد. اطلاعات محرمانه فقط از طریق متغیرهای محیطی مدیریت می‌شوند.
