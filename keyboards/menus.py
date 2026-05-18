# 📁 keyboards/menus.py - نسخه نهایی

import unicodedata

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# متن دکمهٔ ریپلای «آگهی‌های من» — برای مقایسهٔ مطمئن (ZWNJ / نرمال‌سازی یونیکد)
MY_ADVERTS_REPLY_BUTTON_TEXT = "📰 آگهی‌های من"
# همان متن ردیف reply و اینلاین «پیشنهادهای من»
MY_OFFERS_REPLY_BUTTON_TEXT = "📋 پیشنهادهای من"
# قوانین: ردیف ریپلای با متن کامل (عرض یک دکمه)؛ اینلاین حداکثر ~۶۴ کاراکتر
CHANNEL_RULES_REPLY_BUTTON_TEXT = (
    "📜 قوانین و روال کار کانال — لطفاً قبل از معامله مطالعه کنید"
)
CHANNEL_RULES_INLINE_BUTTON_TEXT = "📜 قوانین و روال کار کانال"
FEE_INFO_REPLY_BUTTON_TEXT = "🧾 نرخ کارمزد معاملات (یورو — هر طرف)"
FEE_INFO_INLINE_BUTTON_TEXT = "🧾 نرخ کارمزد معاملات"


def reply_menu_text_matches(label: str, received: str) -> bool:
    """مقایسهٔ متن دکمهٔ reply با تحمل تفاوت‌های جزئی یونیکد در کلاینت‌های تلگرام."""
    def _norm(s: str) -> str:
        return unicodedata.normalize("NFKC", (s or "").strip()).replace("\u200c", "")

    return _norm(received) == _norm(label)

PAYMENT_OPTIONS = ["IBAN", "PayPal", "Wise", "Revolut"]
EXCHANGE_OPTION = "💱 معاوضه Euro به Euro"
CONFIRM_SELECTION_CALLBACK = "confirm_methods"
PAYMENT_SELECTION_TEXT = (
    "💳 روش‌های پرداخت را انتخاب کنید:\n"
    "• چندانتخابی: IBAN / PayPal / Wise / Revolut\n"
    "• تک‌انتخابی: معاوضه Euro به Euro"
)

RECEIVE_SELECTION_TEXT = (
    "📥 روش‌های دریافت را انتخاب کنید:\n"
    "• چندانتخابی: IBAN / PayPal / Wise / Revolut\n"
    "• تک‌انتخابی: معاوضه Euro به Euro"
)


def get_payment_selection_text(operation: str | None) -> str:
    """For BUY, label is 'receive'; for SELL, label is 'pay'."""
    if operation == "خرید":
        return RECEIVE_SELECTION_TEXT
    return PAYMENT_SELECTION_TEXT

CALLBACK_BY_METHOD = {
    "IBAN": "method_iban",
    "PayPal": "method_paypal",
    "Wise": "method_wise",
    "Revolut": "method_revolut",
    EXCHANGE_OPTION: "method_exchange",
}

METHOD_BY_CALLBACK = {v: k for k, v in CALLBACK_BY_METHOD.items()}

def generate_inline_keyboard(selected_methods):
    selected_set = set(selected_methods or [])
    keyboard = []
    row = []
    for option in PAYMENT_OPTIONS:
        label = f"✅ {option}" if option in selected_set else option
        row.append(InlineKeyboardButton(label, callback_data=CALLBACK_BY_METHOD[option]))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    exchange_selected = EXCHANGE_OPTION in selected_set
    label = f"✅ {EXCHANGE_OPTION}" if exchange_selected else EXCHANGE_OPTION
    keyboard.append([InlineKeyboardButton(label, callback_data=CALLBACK_BY_METHOD[EXCHANGE_OPTION])])
    keyboard.append([InlineKeyboardButton("➡️ ادامه با انتخاب فعلی", callback_data=CONFIRM_SELECTION_CALLBACK)])
    keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)


def inline_cancel_keyboard(callback_data: str = "inline_cancel"):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ انصراف از این مرحله", callback_data=callback_data)]]
    )

# 🔘 کیبوردهای reply ثابت
main_menu_keyboard = ReplyKeyboardMarkup([
    ["🚀 ثبت درخواست خدمات"],
    ["🧾 مشاهده پروفایل"],
    [MY_OFFERS_REPLY_BUTTON_TEXT],
    [MY_ADVERTS_REPLY_BUTTON_TEXT],
    [CHANNEL_RULES_REPLY_BUTTON_TEXT],
    [FEE_INFO_REPLY_BUTTON_TEXT],
], resize_keyboard=True)

# ✅ نسخه اینلاینِ منوی اصلی (برای جلوگیری از پیام‌های کاربر)
main_menu_inline_keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("🚀 ثبت درخواست خدمات", callback_data="main_services"),
            InlineKeyboardButton("🧾 مشاهده پروفایل", callback_data="main_profile"),
        ],
        [
            InlineKeyboardButton(MY_OFFERS_REPLY_BUTTON_TEXT, callback_data="main_offers"),
            InlineKeyboardButton(MY_ADVERTS_REPLY_BUTTON_TEXT, callback_data="main_my_adverts"),
        ],
        [InlineKeyboardButton(CHANNEL_RULES_INLINE_BUTTON_TEXT, callback_data="main_rules")],
        [InlineKeyboardButton(FEE_INFO_INLINE_BUTTON_TEXT, callback_data="main_fees")],
    ]
)

def main_menu_keyboard_for_user(telegram_id: int) -> InlineKeyboardMarkup:
    """همان منوی دو دکمه برای همه؛ کاربر محدود با زدن «ثبت درخواست» پیام محدودیت می‌بیند (در هندلر)."""
    _ = telegram_id  # امضا برای سازگاری با فراخوان‌های قبلی
    return main_menu_inline_keyboard

services_inline_keyboard = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("💶 خرید / فروش یورو", callback_data="svc_euro")],
        [InlineKeyboardButton("🔒 اشتراک VPN ایران", callback_data="svc_vpn")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="svc_cancel")],
    ]
)

cancel_keyboard = ReplyKeyboardMarkup([
    ["❌ انصراف"]
], resize_keyboard=True)

fixed_cancel_keyboard = ReplyKeyboardMarkup([
    ["❌ انصراف"]
], resize_keyboard=True)

REGISTRATION_START_BUTTON_TEXT = "📝 ثبت‌نام"
TERMS_ACCEPT_BUTTON_TEXT = "✅ قوانین و روال کار را می‌پذیرم"
TERMS_DECLINE_BUTTON_TEXT = "❌ قبول ندارم"

start_keyboard = ReplyKeyboardMarkup([
    [REGISTRATION_START_BUTTON_TEXT]
], resize_keyboard=True)

start_inline_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton(REGISTRATION_START_BUTTON_TEXT, callback_data="start_begin")]
])

terms_keyboard_colored = ReplyKeyboardMarkup([
    [TERMS_ACCEPT_BUTTON_TEXT],
    [TERMS_DECLINE_BUTTON_TEXT],
], resize_keyboard=True)

terms_inline_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton(TERMS_ACCEPT_BUTTON_TEXT, callback_data="terms_accept")],
    [InlineKeyboardButton(TERMS_DECLINE_BUTTON_TEXT, callback_data="terms_decline")],
])

services_keyboard = ReplyKeyboardMarkup([
    ["💶 خرید/فروش یورو"],
    ["🔒 اشتراک VPN ایران"],
    ["❌ بازگشت"]
], resize_keyboard=True)

admin_menu_keyboard = ReplyKeyboardMarkup([
    ["👥 لیست کاربران", "🔎 جستجوی کاربر"],
    ["➕ افزودن کاربر", "✏️ ویرایش کاربر"],
    ["🗑️ حذف کاربر", "📢 لیست آگهی‌ها"],
    ["➕ ثبت آگهی", "✏️ ویرایش آگهی"],
    ["🧾 ویرایش کارمزد آگهی"],
    ["🗑️ حذف آگهی", "🔎 جستجوی آگهی"],
    ["🗣️ مذاکرات آگهی"],
    ["📋 مدیریت پیشنهاد آگهی"],
    ["🔒 محدودیت دسترسی کاربر"],
    ["⛔️ غیرفعال کردن ربات", "✅ فعال کردن ربات"],
    ["🏠 بازگشت به منو اصلی"]
], resize_keyboard=True)


def admin_panel_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")]])


def admin_restrict_actions_keyboard(target_uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ برداشتن محدودیت", callback_data=f"adm|rxclr|{target_uid}"),
                InlineKeyboardButton("⛔️ محدود کردن", callback_data=f"adm|rxgo|{target_uid}"),
            ],
            [InlineKeyboardButton("🔙 پنل مدیریت", callback_data="adm|panel")],
        ]
    )

# ❗ فقط تایید، بدون برگشت یا انصراف
def inline_confirm_only():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید آگهی", callback_data="confirm_advert")]
    ])

# 🔘 دکمه تایید و برگشت برای پیش‌نمایش آگهی
def inline_confirm_back():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید آگهی", callback_data="confirm_advert"),
            InlineKeyboardButton("🔙 برگشت", callback_data="back")
        ]
    ])