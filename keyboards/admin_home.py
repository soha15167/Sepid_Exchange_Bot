# منوی اینلاین پنل ادمین — جدا از menus.py تا با هر دیپلوی دیده شود.

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_home_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👥 لیست کاربران", callback_data="adm|lu"),
                InlineKeyboardButton("🔎 جستجوی کاربر", callback_data="adm|su"),
            ],
            [
                InlineKeyboardButton("📋 مدیریت پیشنهاد آگهی", callback_data="adm|ofm"),
                InlineKeyboardButton("🎭 پیشنهاد نمایشی", callback_data="adm|pof"),
            ],
            [
                InlineKeyboardButton("➕ افزودن کاربر", callback_data="adm|au"),
                InlineKeyboardButton("✏️ ویرایش کاربر", callback_data="adm|eu"),
            ],
            [
                InlineKeyboardButton("🗑️ حذف کاربر", callback_data="adm|du"),
                InlineKeyboardButton("📢 لیست آگهی‌ها", callback_data="adm|al"),
            ],
            [
                InlineKeyboardButton("➕ ثبت آگهی", callback_data="adm|aa"),
                InlineKeyboardButton("✏️ ویرایش آگهی", callback_data="adm|ea"),
            ],
            [
                InlineKeyboardButton("🗑️ حذف آگهی", callback_data="adm|da"),
                InlineKeyboardButton("🔎 جستجوی آگهی", callback_data="adm|sad"),
            ],
            [InlineKeyboardButton("🗣️ مذاکرات آگهی", callback_data="adm|negv")],
            [InlineKeyboardButton("🔒 محدودیت دسترسی کاربر", callback_data="adm|rx")],
            [
                InlineKeyboardButton("⛔️ غیرفعال کردن ربات", callback_data="adm|bot0"),
                InlineKeyboardButton("✅ فعال کردن ربات", callback_data="adm|bot1"),
            ],
            [InlineKeyboardButton("🔄 ری‌استارت سرویس ربات (سرور)", callback_data="adm|rsvc")],
            [InlineKeyboardButton("🏠 بازگشت به منوی اصلی کاربر", callback_data="adm|exit")],
        ]
    )
