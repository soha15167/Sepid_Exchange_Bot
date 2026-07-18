#!/usr/bin/env python3
"""One-off user/advert diagnostic — usage: python3 _check_user.py <telegram_id>"""
import sqlite3
import sys

uid = int(sys.argv[1])
db = sys.argv[2] if len(sys.argv) > 2 else "eurobot.db"

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()
print("=== USER ===")
print(dict(u) if u else "NOT FOUND")

print("\n=== ADVERTS (latest 10) ===")
for r in conn.execute(
    """
    SELECT rowid, user_id, full_name, euro_amount, rate_toman, operation, methods,
           channel_message_id, channel_chat_id, description, account_country, instant_transfer
    FROM euro_adverts WHERE user_id=? ORDER BY rowid DESC LIMIT 10
    """,
    (uid,),
):
    print(dict(r))

print("\n=== ORPHAN ADS (no channel_message_id) ===")
for r in conn.execute(
    """
    SELECT rowid, euro_amount, rate_toman, operation, full_name
    FROM euro_adverts
    WHERE user_id=? AND (channel_message_id IS NULL OR channel_message_id = '')
    ORDER BY rowid DESC LIMIT 5
    """,
    (uid,),
):
    print(dict(r))
