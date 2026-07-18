#!/usr/bin/env python3
import sqlite3
import sys

uid = int(sys.argv[1])
new_name = sys.argv[2]
db = sys.argv[3] if len(sys.argv) > 3 else "eurobot.db"

conn = sqlite3.connect(db)
row = conn.execute(
    "SELECT display_name FROM users WHERE telegram_id=?", (uid,)
).fetchone()
if not row:
    print("user not found")
    raise SystemExit(1)
print("old:", row[0])
conn.execute(
    "UPDATE users SET display_name=? WHERE telegram_id=?", (new_name, uid)
)
conn.commit()
print("new:", new_name)
print("ok")
