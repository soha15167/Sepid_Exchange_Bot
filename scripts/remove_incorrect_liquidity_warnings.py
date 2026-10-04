"""Remove warnings that were mistakenly inferred from text inside receipt images."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import deal_gate_buyer_receipt_list, deal_gate_update_buyer_receipt
from handlers.deal_gate import (
    _sync_buyer_receipt_reviewer_messages,
    sync_deal_admin_notification,
)


TARGETS = (471, 494)
MANUAL_WARNING = "هشدار: عبارت مدیریت نقدینگی در شرح فیش دیده شد"
MANUAL_OCR_TEXT = "بابت: مدیریت نقدینگی"


async def main() -> None:
    async with Bot(BOT_TOKEN) as bot:
        for offer_id in TARGETS:
            receipts = deal_gate_buyer_receipt_list(offer_id)
            if not receipts:
                continue
            receipt = receipts[0]
            warnings = [
                item
                for item in list(receipt.get("recognition_warnings") or [])
                if str(item) != MANUAL_WARNING
            ]
            fields = {"recognition_warnings": warnings}
            if str(receipt.get("ocr_text") or "") == MANUAL_OCR_TEXT:
                fields["ocr_text"] = ""
            updated = deal_gate_update_buyer_receipt(offer_id, 0, **fields)
            if not updated:
                continue
            await _sync_buyer_receipt_reviewer_messages(
                bot,
                offer_id=offer_id,
                receipt_index=0,
                receipt=updated,
            )
            await sync_deal_admin_notification(bot, offer_id, deal_complete=True)


if __name__ == "__main__":
    asyncio.run(main())
