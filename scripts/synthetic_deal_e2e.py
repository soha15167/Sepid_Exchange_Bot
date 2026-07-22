#!/usr/bin/env python3
"""Run one complete deal lifecycle against a non-production SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    args = parser.parse_args(argv)
    path = Path(args.database).expanduser().resolve()
    if path.name.lower() == "eurobot.db":
        parser.error("refusing to run synthetic lifecycle against eurobot.db")

    from database import db

    db.DB_PATH = str(path)
    db.ensure_schema()
    stamp = int(time.time())
    advert_id = 9_000_000 + stamp % 100_000
    offer_id = 8_000_000 + stamp % 100_000
    buyer_id, seller_id = 7_000_001, 7_000_002
    stages: list[str] = []

    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO euro_adverts
               (id, user_id, euro_amount, rate_toman, operation, status)
               VALUES (?, ?, 100, 210000, 'فروش', 'فعال')""",
            (advert_id, seller_id),
        )
        conn.execute(
            """INSERT INTO advert_offers
               (id, advert_rowid, proposer_telegram_id, rate_toman,
                proposed_euro_amount, created_at, status)
               VALUES (?, ?, ?, 210000, 100, ?, 'accepted')""",
            (offer_id, advert_id, buyer_id, str(stamp)),
        )
    stages.append("offer_accepted")

    db.deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=advert_id,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="pending",
        buyer_response="yes",
        seller_response="yes",
        buyer_confirmed_at=stamp,
        seller_confirmed_at=stamp,
    )
    db.deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=advert_id,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="accounts",
        buyer_accounts_text="SYNTHETIC BUYER ACCOUNT",
        seller_accounts_text="SYNTHETIC SELLER ACCOUNT",
    )
    stages.extend(["parties_confirmed", "accounts_received"])

    db.deal_gate_append_buyer_receipt(
        offer_id,
        entry_type="text",
        text="SYNTHETIC BUYER TOMAN RECEIPT",
        source_message_id=10001,
    )
    db.deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=advert_id,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        buyer_toman_card_sent_at=stamp,
        buyer_toman_settled_at=stamp,
        seller_eur_account_sent_at=stamp,
    )
    stages.extend(["buyer_receipt_recorded", "buyer_toman_settled"])

    db.deal_gate_append_seller_receipt(
        offer_id,
        entry_type="text",
        text="SYNTHETIC SELLER EURO RECEIPT",
        source_message_id=10002,
    )
    if not db.deal_gate_confirm_seller_receipt_buyer(offer_id, 0):
        raise RuntimeError("buyer euro-receipt confirmation failed")
    db.deal_gate_upsert(
        offer_id=offer_id,
        advert_rowid=advert_id,
        buyer_telegram_id=buyer_id,
        seller_telegram_id=seller_id,
        gate_status="completed",
        completed_at=stamp,
    )
    stages.extend(["seller_euro_receipt_recorded", "buyer_confirmed_euro", "admin_euro_settled"])

    if not db.deal_gate_record_seller_toman_delivery(
        offer_id,
        entry_type="text",
        text="SYNTHETIC ADMIN TOMAN RECEIPT",
        delivery_key=f"synthetic:{offer_id}",
        delivered_at=stamp,
    ):
        raise RuntimeError("seller Toman receipt delivery was not recorded")
    if not db.deal_gate_settle_and_close_atomic(
        offer_id, advert_id, settled_at=stamp, require_receipt=True
    ):
        raise RuntimeError("atomic settlement and close failed")
    stages.extend(["admin_paid_seller", "seller_toman_settled", "deal_closed"])

    issues = db.deal_gate_audit(offer_id)
    gate = db.deal_gate_get(offer_id) or {}
    result = {
        "ok": not issues and gate.get("gate_status") == "closed",
        "database": str(path),
        "offer_id": offer_id,
        "advert_id": advert_id,
        "stages": stages,
        "audit_issues": issues,
        "real_users_contacted": False,
        "real_money_used": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
