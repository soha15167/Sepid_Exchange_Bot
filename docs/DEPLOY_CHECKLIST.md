# Deploy checklist / چک‌لیست دیپلوی

## Before deploy / قبل از دیپلوی

- [ ] `.env` on server: `BOT_TOKEN`, `BOT_USERNAME`, `CHANNEL_USERNAME`, `ADVERT_CHANNEL_ID`, `ADMIN_USER_ID`
- [ ] `DATABASE_NAME` unchanged if keeping users/ads (e.g. `eurobot.db`)
- [ ] `TWILIO_*` for SMS OTP (if used)
- [ ] Optional: `LOG_DIR=logs`, `LOG_LEVEL=INFO`, `CHANNEL_SYNC_LIMIT=25`

## Copy files / انتقال (SCP)

Upload changed `handlers/`, `database/`, `utils/`, `messages/`, `config/`, `main.py`.

## On server / روی سرور

```bash
cd /root/telegram_bot_project2
python3 -m pip install -r requirements.txt
python3 -c "from database.db import ensure_schema; ensure_schema()"
sudo systemctl restart telegram-bot   # or your unit name
sudo journalctl -u telegram-bot -n 80 --no-pager
```

## Cron backup (recommended)

```cron
0 3 * * * cd /root/telegram_bot_project2 && python3 scripts/backup_db.py >> logs/backup.log 2>&1
```

## Smoke test / تست

1. `/start` — main menu
2. Admin `/admin` — status line shows فعال/غیرفعال
3. Disable bot — channel notice + user cannot post offer; channel button shows closed
4. Enable bot — broadcast optional; offer button works
5. List users/adverts — pagination if more than 10
6. Submit one test offer — rate limit not hit on first try

## Production migration (@Sepid)

- New bot token + channel IDs in `.env`
- Keep same `eurobot.db` only if intentional
- Announce maintenance window to channel
