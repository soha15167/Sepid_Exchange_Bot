"""One-time recovery of already stored buyer receipts for advert 3605."""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import deal_gate_buyer_receipt_list, deal_gate_get
from handlers.deal_gate import (
    _auto_account_buyer_receipt,
    _notify_buyer_toman_receipt_reviewers,
)


OFFER_ID = 405
ADVERT_ROWID = 3605


async def main() -> None:
    gate = deal_gate_get(OFFER_ID)
    if not gate or int(gate.get("advert_rowid") or 0) != ADVERT_ROWID:
        raise RuntimeError("deal 3605 identity check failed")
    receipts = deal_gate_buyer_receipt_list(OFFER_ID)
    if len(receipts) != 4:
        raise RuntimeError(f"expected 4 stored receipts, found {len(receipts)}")

    async with Bot(BOT_TOKEN) as bot:
        context = SimpleNamespace(bot=bot)
        for index, receipt in enumerate(receipts):
            if receipt.get("reviewer_notify_mids"):
                print(f"skip receipt={index + 1}: reviewer copies already exist")
                continue
            entry_type = str(receipt.get("type") or "photo")
            file_id = str(receipt.get("file_id") or "")
            if not file_id:
                raise RuntimeError(f"receipt {index + 1} has no Telegram file_id")
            await _notify_buyer_toman_receipt_reviewers(
                bot,
                offer_id=OFFER_ID,
                gate=gate,
                receipt_index=index,
                entry_type=entry_type,
                text=str(receipt.get("text") or ""),
                file_id=file_id,
            )
            await _auto_account_buyer_receipt(
                context,
                gate=gate,
                receipt_index=index,
                entry_type=entry_type,
                text=str(receipt.get("text") or ""),
                file_id=file_id,
                file_unique_id=str(receipt.get("file_unique_id") or ""),
            )
            print(f"reprocessed receipt={index + 1}")


if __name__ == "__main__":
    asyncio.run(main())
