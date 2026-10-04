"""One-time correction for the receipt visibly marked مدیریت نقدینگی."""

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


OFFER_ID = 494  # Public channel deal number: 3646
RECEIPT_INDEX = 0
OCR_TEXT = "بابت: مدیریت نقدینگی"
WARNING = "هشدار: عبارت مدیریت نقدینگی در شرح فیش دیده شد"


async def main() -> None:
    receipts = deal_gate_buyer_receipt_list(OFFER_ID)
    if len(receipts) <= RECEIPT_INDEX:
        raise RuntimeError("Target receipt was not found")
    receipt = receipts[RECEIPT_INDEX]
    if int(receipt.get("amount_rial") or 0) != 1_550_000_000:
        raise RuntimeError("Target receipt amount does not match the verified image")
    warnings = list(receipt.get("recognition_warnings") or [])
    if WARNING not in warnings:
        warnings.append(WARNING)
    updated = deal_gate_update_buyer_receipt(
        OFFER_ID,
        RECEIPT_INDEX,
        ocr_text=OCR_TEXT,
        recognition_warnings=warnings,
    )
    if not updated:
        raise RuntimeError("Receipt warning was not saved")
    async with Bot(BOT_TOKEN) as bot:
        await _sync_buyer_receipt_reviewer_messages(
            bot,
            offer_id=OFFER_ID,
            receipt_index=RECEIPT_INDEX,
            receipt=updated,
        )
        await sync_deal_admin_notification(bot, OFFER_ID, deal_complete=True)


if __name__ == "__main__":
    asyncio.run(main())
