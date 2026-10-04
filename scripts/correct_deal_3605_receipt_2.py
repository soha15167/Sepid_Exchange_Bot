"""Correct the visually verified amount of deal 3605 receipt 2."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import (
    deal_gate_buyer_receipt_list,
    deal_gate_get,
    deal_gate_update_buyer_receipt,
)
from handlers.deal_gate import (
    _close_deal_receipt_previews,
    _send_deal_receipt_review_preview,
    _sync_buyer_receipt_reviewer_messages,
    sync_deal_admin_notification,
)


OFFER_ID = 405
ADVERT_ROWID = 3605
RECEIPT_INDEX = 1
OLD_AMOUNT_RIAL = 1_778_250_000
CORRECT_AMOUNT_RIAL = 1_778_750_000


async def main() -> None:
    gate = deal_gate_get(OFFER_ID)
    if not gate or int(gate.get("advert_rowid") or 0) != ADVERT_ROWID:
        raise RuntimeError("deal 3605 identity check failed")
    receipts = deal_gate_buyer_receipt_list(OFFER_ID)
    if len(receipts) != 4:
        raise RuntimeError(f"expected 4 receipts, found {len(receipts)}")
    old = receipts[RECEIPT_INDEX]
    current = int(old.get("amount_rial") or 0)
    if current not in {OLD_AMOUNT_RIAL, CORRECT_AMOUNT_RIAL}:
        raise RuntimeError(f"unexpected current receipt amount: {current}")

    expected = max(int(item.get("expected_rial") or 0) for item in receipts)
    updated = deal_gate_update_buyer_receipt(
        OFFER_ID,
        RECEIPT_INDEX,
        amount_rial=CORRECT_AMOUNT_RIAL,
        recognized_amount_rial=CORRECT_AMOUNT_RIAL,
        expected_rial=expected,
        reviewer_received_at=0,
        reviewer_received_by=0,
    )
    if not updated:
        raise RuntimeError("receipt amount update failed")

    running = 0
    for index, item in enumerate(deal_gate_buyer_receipt_list(OFFER_ID)):
        if (item.get("accounting_status") or "").strip().lower() in {
            "duplicate",
            "rejected",
        }:
            continue
        running += max(0, int(item.get("amount_rial") or 0))
        recalculated = deal_gate_update_buyer_receipt(
            OFFER_ID,
            index,
            expected_rial=expected,
            cumulative_rial=running,
            remaining_rial=max(0, expected - running),
        )
        if index == RECEIPT_INDEX and recalculated:
            updated = recalculated

    async with Bot(BOT_TOKEN) as bot:
        for index, item in enumerate(deal_gate_buyer_receipt_list(OFFER_ID)):
            await _close_deal_receipt_previews(bot, item)
            await _send_deal_receipt_review_preview(
                bot, gate=gate, receipt_index=index, receipt=item
            )
        latest = deal_gate_buyer_receipt_list(OFFER_ID)[RECEIPT_INDEX]
        await _sync_buyer_receipt_reviewer_messages(
            bot,
            offer_id=OFFER_ID,
            receipt_index=RECEIPT_INDEX,
            receipt=latest,
        )
        await sync_deal_admin_notification(bot, OFFER_ID, deal_complete=True)

    total = running
    if total != expected:
        raise RuntimeError(f"corrected total mismatch: {total} != {expected}")
    print(f"CORRECTED receipt=2 amount={CORRECT_AMOUNT_RIAL} total={total}")


if __name__ == "__main__":
    asyncio.run(main())
