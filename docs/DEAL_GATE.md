# Deal Gate | دروازه معامله

**EN:** Detailed guide for **final confirmation** and **payment coordination** after an offer is accepted.  
**FA:** راهنمای **تأیید نهایی** و **هماهنگی واریز** پس از پذیرش پیشنهاد.

**Implementation / پیاده‌سازی:** `handlers/deal_gate.py`

---

## Roles on buy & sell ads | نقش‌ها در آگهی خرید و فروش

**EN:** Euro buyer/seller Telegram IDs come from advert `operation` and the offer row — function `_offer_buyer_seller_telegram_ids` in `handlers/offers.py`.

**FA:** شناسهٔ خریدار/فروشنده یورو از `operation` آگهی و ردیف پیشنهاد — تابع `_offer_buyer_seller_telegram_ids` در `handlers/offers.py`.

| Advert type | EN: Euro buyer | EN: Euro seller | FA: خریدار یورو | FA: فروشنده یورو |
|-------------|----------------|-----------------|-----------------|------------------|
| **Sell** (owner sells) | Proposer | Ad owner | پیشنهاددهنده | صاحب آگهی |
| **Buy** (owner buys) | Ad owner | Proposer | صاحب آگهی | پیشنهاددهنده |

**EN:** Toman amounts use `buyer_deposit_toman_amount` and compact financial HTML in the admin message.  
**FA:** مبالغ تومان با `buyer_deposit_toman_amount` و خلاصه مالی در پیام ادمین.

---

## Full flowchart | فلوچارت کل

```mermaid
flowchart TB
    subgraph phase0 ["Accept | پذیرش"]
        A["Accept"] --> B["start_deal_final_gate"]
        B --> C["Yes/No both parties"]
        C --> D{"Both yes?"}
        D -->|No| X["Cancel / admin"]
        D -->|Yes| E["Accounts"]
    end

    subgraph phase1 ["Accounts | حساب"]
        E --> F["EUR accounts"]
        F --> G["Admin message complete"]
    end

    subgraph phase2 ["Buyer Toman | تومان خریدار"]
        G --> H["Card to buyer"]
        H --> I["Buyer receipts (optional)"]
        I --> J["Toman settled (admin)"]
    end

    subgraph phase3 ["Seller Euro | یورو فروشنده"]
        J --> K["EUR account to seller"]
        K --> L["Euro receipts"]
        L --> M["Euro landed confirm"]
        M --> N["Notify seller"]
    end

    subgraph phase4 ["Seller Toman | تومان فروشنده"]
        N --> P["Admin pays seller + receipt"]
        P --> Q["Seller confirms toman + close"]
        Q --> R["Deal closed"]
    end
```

**FA:** ادمین می‌تواند «تومان نشست» را بدون فیش خریدار هم بزند. پایان معامله با تأیید فروشنده (`deal|stomcfm|`) یا بستن دستی ادمین (`adm|dg|close|`) انجام می‌شود.

---

## Admin message sync | همگام‌سازی پیام ادمین

**EN:** `sync_deal_admin_notification` with `resend_fresh=True` (default) deletes the previous admin text + album and sends a fresh message at the bottom of the chat.

**FA:** با هر به‌روزرسانی، پیام قبلی + آلبوم حذف و نسخهٔ جدید پایین چت ارسال می‌شود. شناسهٔ پیام‌ها در `admin_notify_mids` و `admin_notify_photo_mids` ذخیره می‌شود (شامل `album`, `by_fid`, `mode`).

**Scripts:**

| Script | EN | FA |
|--------|----|----|
| `scripts/resync_deal_admin_message.py` | Resync one/more offers | یک یا چند offer |
| `scripts/resync_all_active_deal_admin_messages.py` | All `gate_status=completed` | همهٔ معاملات در فاز واریز |
| `scripts/resend_seller_stom_close_prompt.py` | Resend seller close button | ارسال مجدد دکمهٔ پایان به فروشنده |

---

## gate_status values | وضعیت gate_status

| Value | EN | FA |
|-------|----|----|
| `pending` | Waiting final yes/no | انتظار تأیید نهایی |
| `accounts` | Collecting EUR accounts | جمع حساب |
| `completed` | Both accounts; payment phase | تکمیل حساب؛ واریز |
| `closed` | Deal finished | معامله بسته شد |
| (other) | Admin decision after 2h escalation | تصمیم ادمین |

---

## Admin buttons (staged) | دکمه‌های ادمین

**EN:** On the **main deal message** (`sync_deal_admin_notification`), buttons appear by stage.

**FA:** روی **پیام اصلی معامله**، دکمه‌ها مرحله‌ای نمایش داده می‌شوند.

| Button | Callback | EN: When shown | FA: شرط |
|--------|----------|----------------|---------|
| Toman card to buyer | `adm\|pay\|{id}` | Deal complete | همیشه پس از تکمیل |
| Toman settled | `adm\|tomset\|{id}` | Card sent, not settled | کارت فرستاده، نشست نخورده |
| Euro settled (admin) | `adm\|eurcfm\|{id}\|{idx}` | Unconfirmed euro receipt | فیش یورو بدون تأیید |
| Toman receipt to seller | `adm\|stom\|{id}\|go` | All euro receipts confirmed | همه یورو تأیید شده |
| Close deal | `adm\|dg\|close\|{id}` | Always (fallback) | همیشه (دستی) |
| Bot messages log | `adm\|outlog\|{id}` | Always | همیشه |

---

## Party callbacks | callback طرفین

| Party | EN: Action | FA: عمل | Callback |
|-------|------------|---------|----------|
| Buyer | Toman receipt | فیش تومان | `deal\|rcpt\|{oid}\|go` / `cancel` |
| Seller | Euro receipt | فیش یورو | `deal\|srcpt\|{oid}\|go` / `cancel` |
| Buyer | Euro landed | یورو نشست | `deal\|eurset\|{oid}\|{idx}` |
| Seller | Toman received + close | تومان نشست — پایان | `deal\|stomcfm\|{oid}` |

**FA:** پس از `stom`، پیام ادمین بنر **«منتظر تأیید فروشنده»** نشان می‌دهد. اگر فروشنده ظرف ۸ ساعت نزند، یادآوری ارسال می‌شود و **هر ۸ ساعت** تکرار می‌شود. وقتی فروشنده می‌بندد، ادمین‌ها اعلان جدا می‌گیرند.

---

## DB columns | ستون‌های offer_deal_gates

| Column | EN | FA |
|--------|----|----|
| `buyer_toman_card_sent_at` | Card sent to buyer timestamp | زمان ارسال کارت |
| `buyer_receipt_log` | JSON buyer toman receipts | فیش تومان خریدار |
| `buyer_toman_settled_at` | Admin confirmed toman settled | تومان نشست |
| `seller_eur_account_sent_at` | EUR account sent to seller | حساب یورو به فروشنده |
| `seller_receipt_log` | JSON euro receipts + `buyer_confirmed_at` | فیش یورو + تأیید |
| `seller_toman_admin_log` | JSON admin toman receipts to seller | فیش تومان به فروشنده |
| `seller_toman_settled_at` | Seller confirmed toman + deal end | تأیید فروشنده |
| `admin_notify_mids` | JSON admin chat → message id | پیام اصلی ادمین |
| `admin_notify_photo_mids` | JSON album mids + by_fid + mode | آلبوم reply ادمین |

---

## PTB routing | مسیریابی main.py

| Group | Router | EN | FA |
|-------|--------|----|----|
| 0 | `deal_gate_group0_text_router` | Receipts, accounts, admin stom text | متن فیش و حساب |
| 4 | `deal_gate_group0_photo_router` | Receipt photos | عکس فیش |
| — | `deal_gate_callback` | `deal\|*`, `adm\|dg\|*` | callback طرفین |
| — | `deal_admin_*` | `adm\|pay\|`, `tomset`, `eurcfm`, `stom` | callback ادمین |

---

## Main menu after actions | منوی اصلی

**EN:** After receipt upload, cancel, or confirm — `_show_user_main_menu`: `admin_home_inline_keyboard` for admins, `main_menu_inline_keyboard` for users.

**FA:** پس از فیش/انصراف/تأیید — منوی اصلی؛ ادمین پنل admin_home، کاربران منوی عادی. پس از آپلود فیش ادمین، پیام جداگانهٔ «به آلبوم اضافه شد» ارسال نمی‌شود — فقط sync پیام اصلی.

## Reliability and notification policy | پایداری و سیاست اعلان

- Critical seller-receipt and terminal status messages use `deal_delivery_queue` with a unique
  `dedupe_key`. Failed Telegram deliveries are retried silently with exponential backoff.
- A seller Toman receipt is added to `seller_toman_admin_log` and enables final confirmation only
  after Telegram accepts the receipt. Queue completion and receipt recording share one DB commit.
- Close updates the offer, advert, and gate status in one transaction. Reactivation archives the
  complete gate in `offer_deal_gate_archive` before deleting the active gate.
- Repeated callbacks and repeated Telegram updates reuse the same delivery/source key and do not
  create duplicate receipts or user messages.
- Users receive only essential financial/status messages. The seller receives at most one delayed
  final-confirmation reminder; retry failures and repair details remain admin-only.
- `deal_gate_audit` reports invariant violations. `deal_gate_repair_safe` only repairs metadata and
  never invents a payment or confirmation. Admins can run it with `adm|dgs|repair|{offer_id}`.
- Financial callbacks are authorized against the persisted party and current stage. Clicking an
  old button removes that stale keyboard instead of leaving a reusable action behind.
- `adm|dgs|problems` is a passive admin-only work queue for stuck stages, failed delivery, and
  state mismatches. It is evaluated only when opened and never sends user notifications.
- Sensitive admin actions (payment overrides, close, reopen, and repair) require a fresh second
  confirmation that expires after two minutes. Support operators configured with
  `DEAL_SUPPORT_ADMIN_ID` remain read-only for financial actions.
- Admin deal details include a chronological timeline and UTF-8 export. Receipt consistency
  checks create admin-only warnings and never approve or reject a payment automatically.
- Verified SQLite backups run every six hours. Financial artifacts from closed deals are redacted
  after `DEAL_FINANCIAL_RETENTION_DAYS` while milestone history is preserved.
- The passive operations-health page shows database integrity, delivery queue state, problem-deal
  count, last verified backup, privacy cleanup, and process start time.
- Critical delivery retries honor Telegram `RetryAfter`; concurrent settlement remains a
  single-winner atomic transaction.
