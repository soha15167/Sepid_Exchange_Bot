# Deal Gate — دروازه معامله / راهنمای کامل

مستند تفصیلی فلو **تأیید نهایی** و **هماهنگی واریز** پس از پذیرش پیشنهاد.  
پیاده‌سازی اصلی: `handlers/deal_gate.py`

---

## نقش‌ها در آگهی خرید و فروش

خریدار/فروشندهٔ **یورو** از نوع آگهی (`operation`) و پیشنهاد تعیین می‌شود — تابع  
`_offer_buyer_seller_telegram_ids` در `handlers/offers.py`:

| نوع آگهی | خریدار یورو (`buyer_telegram_id`) | فروشنده یورو (`seller_telegram_id`) |
|----------|-----------------------------------|-------------------------------------|
| **فروش** (صاحب می‌فروشد) | پیشنهاددهنده | صاحب آگهی |
| **خرید** (صاحب می‌خرد) | صاحب آگهی | پیشنهاددهنده |

مبالغ تومان (امانت خریدار، واریز نهایی به فروشنده) با `buyer_deposit_toman_amount` و خلاصهٔ مالی در اعلان ادمین محاسبه می‌شود.

---

## فلوچارت کل (از پذیرش تا پایان)

```mermaid
flowchart TB
    subgraph phase0 [۰ — پذیرش پیشنهاد]
        A[صاحب آگهی پیشنهاد را می‌پذیرد] --> B[start_deal_final_gate]
        B --> C[پیام بله/خیر به خریدار و فروشنده]
        C --> D{هر دو بله?}
        D -->|خیر| X[لغو / ادمین]
        D -->|بله| E[gate_status = accounts]
    end

    subgraph phase1 [۱ — حساب‌ها]
        E --> F[هر طرف حساب یورو را می‌فرستد]
        F --> G[sync_deal_admin_notification — تکمیل شد]
    end

    subgraph phase2 [۲ — تومان خریدار]
        G --> H[ادمین: کارت واریز تومان به خریدار]
        H --> I[خریدار: ارسال فیش تومان — چندباره]
        I --> J[ادمین: تومان نشست]
    end

    subgraph phase3 [۳ — یورو فروشنده]
        J --> K[خودکار: حساب یورو خریدار → فروشنده]
        K --> L[فروشنده: فیش یورو]
        L --> M[کپی مخفی به خریدار + دکمه یورو نشست]
        M --> N{تأیید نشستن}
        N -->|خریدار یا ادمین| O[اعلان فروشنده + به‌روز پیام ادمین]
    end

    subgraph phase4 [۴ — تومان فروشنده]
        O --> P[ادمین: واریز دستی تومان به فروشنده]
        P --> Q[ادمین: فیش تومان به فروشنده]
        Q --> R[پایان هماهنگی ربات]
    end

    phase0 --> phase1 --> phase2 --> phase3 --> phase4
```

---

## وضعیت‌های `gate_status`

| مقدار | معنی |
|--------|------|
| `pending` | انتظار تأیید نهایی بله/خیر |
| `accounts` | جمع‌آوری حساب یورو |
| `completed` | هر دو حساب ثبت شد؛ هماهنگی واریز |
| (سایر) | تصمیم ادمین پس از اسکیلیشن ۲ ساعته |

---

## دکمه‌های ادمین (مرحله‌ای)

روی **همان پیام اصلی معامله** (`sync_deal_admin_notification`):

| دکمه | Callback | شرط نمایش |
|------|----------|------------|
| 💳 ارسال کارت واریز تومان به خریدار | `adm\|pay\|{offer_id}` | همیشه در `completed` |
| ✅ تومان نشست | `adm\|tomset\|{offer_id}` | کارت فرستاده، هنوز `buyer_toman_settled_at` خالی |
| ✅ یورو نشست (ادمین) | `adm\|eurcfm\|{offer_id}\|{idx}` | فیش یورو بدون تأیید |
| 📎 فیش تومان به فروشنده | `adm\|stom\|{offer_id}\|go` | همه فیش‌های یورو تأیید شده |
| 📋 پیام‌های ربات | `adm\|outlog\|{offer_id}` | همیشه |

---

## Callback طرفین

| طرف | عمل | Callback |
|-----|-----|----------|
| خریدار | فیش تومان | `deal\|rcpt\|{oid}\|go` / `cancel` |
| فروشنده | فیش یورو | `deal\|srcpt\|{oid}\|go` / `cancel` |
| خریدار | یورو نشست | `deal\|eurset\|{oid}\|{idx}` |

---

## ستون‌های مهم `offer_deal_gates`

| ستون | کاربرد |
|--------|--------|
| `buyer_toman_card_sent_at` | زمان ارسال کارت به خریدار |
| `buyer_receipt_log` | JSON آرایه فیش‌های تومان خریدار |
| `buyer_toman_settled_at` | ادمین «تومان نشست» زده |
| `seller_eur_account_sent_at` | حساب یورو به فروشنده رفته |
| `seller_receipt_log` | JSON فیش یورو + `buyer_confirmed_at` |
| `seller_toman_admin_log` | JSON فیش تومان ادمین به فروشنده |
| `admin_notify_mids` | JSON `{chat_id: message_id}` پیام اصلی ادمین |

---

## مسیریابی PTB (`main.py`)

| Group | Router | اولویت |
|-------|--------|--------|
| 0 | `deal_gate_group0_text_router` | فیش ادمین→فروشنده، فیش طرفین، حساب |
| 4 | `deal_gate_group0_photo_router` | عکس فیش |
| — | `deal_gate_callback` | `deal\|*` و `adm\|dg\|*` |
| — | `deal_admin_*` callbacks | `adm\|pay\|`, `tomset`, `eurcfm`, `stom`, … |

---

## منوی اصلی

پس از ارسال فیش، انصراف، یا تأیید: `_show_user_main_menu` — برای ادمین `admin_home_inline_keyboard`، برای کاربران `main_menu_inline_keyboard`.
