# Code overview / راهنمای کد

Bilingual guide to the **Sepid Exchange Bot** codebase (English + Persian in every section).  
راهنمای دو‌زبانه برای درک ساختار پروژه — هر بخش **EN + FA**.

**Convention / قرارداد:** README, `docs/*.md`, and `# Section N | بخش N` banners in Python files.  
**Commit messages / کامیت:** English subject + Persian explanation in body when documenting.

---

## Architecture / معماری

```
User (Telegram)  →  main.py (handlers)  →  handlers/* (flows)
                              ↓
                    database/db.py (SQLite)
                              ↓
                    Channel @Sepid_Exchange (posts)
```

| Layer | EN | FA |
|--------|----|----|
| **Entry** | `main.py` registers commands, callbacks, message routers | ثبت دستورات و مسیریابی پیام‌ها |
| **State** | `context.user_data["state"]` + `models.enums.UserState` | مرحلهٔ فعلی هر کاربر در فلو |
| **Memory** | `state.user_data_store` — per-user draft (methods, operation) | پیش‌نویس موقت (روش پرداخت، خرید/فروش) |
| **Persistence** | `eurobot.db` — users, adverts, offers | کاربران، آگهی‌ها، پیشنهادها |
| **UI** | `keyboards/menus.py` — inline & reply keyboards | دکمه‌های منو |

---

## File map / نقشهٔ فایل‌ها

### `main.py`
- **EN:** Application bootstrap; handler groups; `euro_flow_router` dispatches by `UserState`.
- **FA:** راه‌اندازی ربات؛ گروه −۱ برای محدودیت/ثبت‌نام؛ روتر متن بر اساس state.

### `config/settings.py`
- **EN:** Loads `.env` — token, channel ID, DB path, `ADVERT_ID_START`, Twilio, admin ID.
- **FA:** تنظیمات محیطی؛ بدون commit کردن `.env`.

### `database/db.py`
- **EN:** SQLite access; `ensure_schema()` migrations; CRUD for users, `euro_adverts`, `advert_offers`.
- **FA:** دیتابیس؛ migration خودکار ستون‌ها؛ توابع لیست/درج/حذف پیشنهاد.

Key tables / جداول اصلی:

| Table | EN | FA |
|-------|----|----|
| `users` | Registered users, display name, phone, restrictions | کاربران ثبت‌نام‌شده |
| `euro_adverts` | Channel ads (`rowid` = ad number shown in channel) | آگهی‌های کانال |
| `advert_offers` | Offers on an ad (rate, status, proposer country) | پیشنهادها روی آگهی |
| `settings` | e.g. `bot_enabled` | تنظیمات سراسری ربات |

### `models/enums.py`
- **EN:** All `UserState` values used in flows (registration, euro, exchange, offer, admin).
- **FA:** نام stateها برای هر مرحله (ثبت‌نام، آگهی، پیشنهاد، ادمین).

### `state.py`
- **EN:** `user_data_store` dict — survives across steps within a session (not SQLite).
- **FA:** دیکشنری موقت per user؛ مثلاً `methods`, `operation`.

---

## Handlers / هندلرها

| Module | EN | FA |
|--------|----|----|
| `start_flow.py` | `/start`, terms accept/decline | خوش‌آمد، قوانین |
| `registration.py` | ConversationHandler: name → SMS verify → save user | ثبت‌نام چندمرحله‌ای |
| `access_gate.py` | Blocks restricted/unregistered users from menu | گیت دسترسی |
| `services.py` | Main menu → buy/sell or VPN entry | منوی خدمات |
| `callbacks.py` | Payment method multi-select (IBAN, PayPal, …) | انتخاب روش پرداخت |
| `euro_flow.py` | Buy/sell with Toman rate → post to channel | خرید/فروش یورو |
| `exchange_flow.py` | Euro-to-Euro exchange ads | معاوضه Euro به Euro |
| `offers.py` | Offer gate, rate, country, preview, owner actions; admin deal HTML | پیشنهاد + اعلان ادمین |
| `deal_gate.py` | Final gate, accounts, toman/euro receipts, admin payment buttons | **دروازه معامله** — [DEAL_GATE.md](DEAL_GATE.md) |
| `deal_outbound.py` | Log/replay bot messages to deal parties | لاگ پیام‌های معامله |
| `channel_info.py` | Channel rules & fee schedule text | قوانین و کارمزد |
| `user_adverts.py` | User's own ads list / edit entry | آگهی‌های من |
| `admin.py` | Admin panel: users, ads, offers, restrictions | پنل ادمین |
| `error_handler.py` | Global error logging/reply | خطاهای عمومی |

---

## Utils / ابزارها

| Module | EN | FA |
|--------|----|----|
| `telegram_utils.py` | Main menu anchor, registration welcome, cleanup message IDs | منوی اصلی، پاکسازی پیام |
| `channel_format.py` | RTL payment methods layout; country label | قالب متن کانال |
| `euro_fees.py` | Fee tiers per euro amount | پلکان کارمزد |
| `sms.py` | Twilio verification codes | SMS ثبت‌نام |
| `validators.py` | Email/phone validation | اعتبارسنجی |

---

## Typical flows / فلوهای معمول

### Registration / ثبت‌نام
1. `send_registration_welcome` → terms  
2. `terms_accept` → ConversationHandler  
3. `save_user` → main menu  

### Post advert (buy/sell) / ثبت آگهی
1. Services → buy or sell  
2. Select receive/pay methods → country (sell) → amount → rate → description  
3. `confirm_and_post_advert` → `ADVERT_CHANNEL_ID`  

### Offer on channel post / پیشنهاد
1. Deep link or button → `deliver_offer_proposal_gate`  
2. Agree or custom amount/rate  
3. `insert_advert_offer` → notify owner & admins  

### Deal Gate / معامله پس از پذیرش
See **[DEAL_GATE.md](DEAL_GATE.md)** for full flowcharts.  
Short path: `start_deal_final_gate` → accounts → admin card → buyer toman receipt → **تومان نشست** → seller euro → buyer **یورو نشست** → admin toman receipt to seller.

---

## Conventions / قراردادها

- **EN:** Channel ad number = SQLite `rowid` of `euro_adverts`, not column `id`.  
- **FA:** «آگهی شماره N» همان `rowid` است.  
- **EN:** Callback data often `prefix|id` (e.g. `offer_gate_agree|98`).  
- **FA:** دادهٔ callback برای دکمه‌های اینلاین.  
- **EN:** `_RTL = "\u200f"` prefix for Persian HTML in Telegram.  
- **FA:** برای نمایش درست راست‌به‌چپ در پیام HTML.

---

## Maintenance scripts / اسکریپت‌ها

- `scripts/init_fresh_database.py` — backup old DB, empty schema, set `ADVERT_ID_START`.  
  پشتیبان‌گیری و دیتابیس تازه با شمارهٔ اولیهٔ آگهی.
