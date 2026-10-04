"""One-time recovery: send the missed media confirmation for deal 3605, receipt 2."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import deal_gate_buyer_receipt_list, deal_gate_get
from handlers.deal_gate import _broadcast_buyer_receipt_confirmation


OFFER_ID = 405
RECEIPT_INDEX = 1


async def main() -> None:
    gate = deal_gate_get(OFFER_ID)
    receipts = deal_gate_buyer_receipt_list(OFFER_ID)
    if not gate or len(receipts) <= RECEIPT_INDEX:
        raise RuntimeError("Deal 3605 receipt 2 was not found")
    receipt = receipts[RECEIPT_INDEX]
    if not int(receipt.get("reviewer_received_at") or 0):
        raise RuntimeError("Receipt 2 has not been confirmed; recovery was not sent")
    if int(receipt.get("amount_rial") or 0) != 1_778_750_000:
        raise RuntimeError("Receipt 2 amount does not match the verified amount")
    async with Bot(BOT_TOKEN) as bot:
        await _broadcast_buyer_receipt_confirmation(
            bot,
            offer_id=OFFER_ID,
            receipt_index=RECEIPT_INDEX,
            receipt=receipt,
        )


if __name__ == "__main__":
    asyncio.run(main())
