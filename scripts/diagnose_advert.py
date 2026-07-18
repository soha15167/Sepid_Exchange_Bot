#!/usr/bin/env python3
"""Diagnose deal state by advert_rowid or offer_id."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import sqlite3

from config.settings import DB_PATH
from database.db import deal_gate_get, get_advert_offer_joined, get_euro_advert_by_rowid
from handlers.deal_gate import _buyer_toman_card_delivered
from handlers.offers import _offer_buyer_seller_telegram_ids


def _resolve(raw: int) -> list[int]:
    row = get_advert_offer_joined(raw)
    if row:
        return [int(raw)]
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT id FROM advert_offers WHERE advert_rowid = ? ORDER BY id",
            (int(raw),),
        )
        return [int(r[0]) for r in cur.fetchall()]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: diagnose_advert.py <advert_rowid|offer_id>")
        return 1
    raw = int(sys.argv[1])
    offer_ids = _resolve(raw)
    if not offer_ids:
        print(f"no offers for id {raw}")
        return 1
    print(f"resolved {raw} -> offer_ids {offer_ids}\n")
    for oid in offer_ids:
        row = get_advert_offer_joined(oid)
        gate = deal_gate_get(oid)
        advert = get_euro_advert_by_rowid(int(row["advert_rowid"])) if row else None
        print("=" * 60)
        print(f"OFFER {oid} | advert {row.get('advert_rowid') if row else '?'} | status {row.get('status') if row else '?'}")
        if advert:
            print(f"  operation: {advert.get('operation')} | owner: {advert.get('user_id')}")
        if row and advert:
            buyer_id, seller_id = _offer_buyer_seller_telegram_ids(advert, row)
            print(f"  buyer_tg: {buyer_id} | seller_tg: {seller_id}")
            print(f"  proposer: {row.get('proposer_telegram_id')} | owner_id: {row.get('owner_id')}")
        if gate:
            g = dict(gate)
            for k in ("buyer_accounts_text", "seller_accounts_text", "admin_notify_mids", "admin_notify_photo_mids", "buyer_receipt_log", "seller_receipt_log"):
                v = g.get(k)
                if v and len(str(v)) > 100:
                    g[k] = str(v)[:100] + "..."
            print("  GATE:", json.dumps(g, ensure_ascii=False, indent=4, default=str))
            bid = int(gate.get("buyer_telegram_id") or 0)
            card_at = int(gate.get("buyer_toman_card_sent_at") or 0)
            delivered = _buyer_toman_card_delivered(oid, bid)
            print(f"  buyer_toman_card_sent_at: {card_at}")
            print(f"  buyer_toman_card_delivered (outbound log): {delivered}")
        else:
            print("  GATE: none")
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            logs = conn.execute(
                """
                SELECT id, recipient_telegram_id, party, tag, msg_type, created_at
                FROM offer_bot_outbound_log WHERE offer_id = ? ORDER BY id
                """,
                (oid,),
            ).fetchall()
        print("  OUTBOUND LOG:")
        if not logs:
            print("    (empty)")
        for lg in logs:
            print(f"    {dict(lg)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
