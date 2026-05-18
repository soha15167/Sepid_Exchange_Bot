# 📁 handlers/exchange_debug_handler.py - فایل کامل برای بررسی Inline Keyboard Expected

from telegram import Update
from telegram.ext import ContextTypes

# لاگ‌گیری جزئیات Query

def log_query_info(query):
    print("================ DEBUG ================")
    print("query.message.text:", query.message.text)
    print("query.message.reply_markup:", query.message.reply_markup)
    print("query.data:", query.data)
    print("================ END DEBUG ================")

# هندلر تستی — در حالت پیش‌فرض غیرفعال؛ قبلاً برای همهٔ callbackها answer می‌زد و با هندلرهای
# واقعی (مثلاً main_offers) تداخل و خطا ایجاد می‌کرد.

async def debug_inline_keyboard_expected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os

    if os.environ.get("TG_DEBUG_CALLBACKS", "").strip() not in ("1", "true", "yes"):
        return
    query = update.callback_query
    if not query:
        return
    try:
        log_query_info(query)
    except Exception:
        pass
    try:
        await query.answer("Debug logged")
    except Exception:
        pass
