"""One-time resend of advert 3598's saved buyer receipt to its reviewer."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config.settings import BOT_TOKEN, DEAL_RECEIPT_REVIEWER_IDS


async def main() -> None:
    conn = sqlite3.connect("eurobot.db")
    conn.row_factory = sqlite3.Row
    gate = conn.execute(
        "SELECT offer_id, advert_rowid, buyer_receipt_log FROM offer_deal_gates WHERE advert_rowid = ?",
        (3598,),
    ).fetchone()
    if not gate:
        raise RuntimeError("advert 3598 gate not found")
    receipts = json.loads(gate["buyer_receipt_log"] or "[]")
    offer_id = int(gate["offer_id"])
    async with Bot(BOT_TOKEN) as bot:
        for reviewer_id in DEAL_RECEIPT_REVIEWER_IDS:
            for index, receipt in enumerate(receipts):
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ دریافت شد", callback_data=f"adm|tomset|{offer_id}|{index}"
                    )
                ]])
                caption = (
                    "📎 <b>فیش واریز خریدار</b>\n\n"
                    f"معامله <b>3598</b> · فیش شماره <b>{index + 1}</b>\n\n"
                    "پس از بررسی، دکمهٔ <b>دریافت شد</b> را بزنید.\n"
                    "اطلاعات حساب خریدار فقط پس از تأیید همهٔ فیش‌ها برای فروشنده ارسال می‌شود."
                )
                file_id = str(receipt.get("file_id") or "")
                if file_id:
                    sent = await bot.send_photo(
                        chat_id=int(reviewer_id), photo=file_id, caption=caption,
                        parse_mode=ParseMode.HTML, reply_markup=markup,
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=int(reviewer_id), text=caption,
                        parse_mode=ParseMode.HTML, reply_markup=markup,
                    )
                print(f"sent reviewer={reviewer_id} message_id={sent.message_id} offer={offer_id} receipt={index}")


if __name__ == "__main__":
    asyncio.run(main())
