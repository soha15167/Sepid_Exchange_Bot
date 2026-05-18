# 📁 handlers/error_handler.py - گرفتن خطاهای دقیق هنگام اجرای بات

from telegram import Update
from telegram.ext import ContextTypes
import traceback

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("========== EXCEPTION ==========")
    print("Update:", update)
    print("Exception:", context.error)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    print("===============================")
