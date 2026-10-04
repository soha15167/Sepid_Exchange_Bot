"""Refresh deal 3605 reviewer copies after keyboard changes."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import deal_gate_buyer_receipt_list, deal_gate_get
from handlers.deal_gate import _sync_buyer_receipt_reviewer_messages


async def main() -> None:
    gate = deal_gate_get(405)
    if not gate or int(gate.get("advert_rowid") or 0) != 3605:
        raise RuntimeError("deal 3605 identity check failed")
    receipts = deal_gate_buyer_receipt_list(405)
    if len(receipts) != 4:
        raise RuntimeError(f"expected 4 receipts, found {len(receipts)}")
    async with Bot(BOT_TOKEN) as bot:
        for index, receipt in enumerate(receipts):
            await _sync_buyer_receipt_reviewer_messages(
                bot, offer_id=405, receipt_index=index, receipt=receipt
            )
            print(f"refreshed receipt={index + 1}")


if __name__ == "__main__":
    asyncio.run(main())
