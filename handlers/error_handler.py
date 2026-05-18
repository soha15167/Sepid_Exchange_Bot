"""
handlers/error_handler.py — Global errors / خطاهای سراسری

EN: Logs traceback; optional user-facing error message.
FA: ثبت خطا هنگام exception در هندلرها.
"""

from telegram import Update
from telegram.ext import ContextTypes
import traceback

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("========== EXCEPTION ==========")
    print("Update:", update)
    print("Exception:", context.error)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    print("===============================")
