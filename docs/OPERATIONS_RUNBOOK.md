# Sepid operations runbook

These checks are intentionally quiet: they write status to stdout/journal and do
not notify deal users. Connect the systemd unit failure state to the operator's
existing monitoring service if external alerts are desired.

## Pre-deployment gate

1. Create a verified backup: `python scripts/backup_db.py`.
2. Run `python scripts/ops_check.py restore-drill <newest-backup>`.
3. Run `python scripts/security_audit.py`.
4. Run the test suite and `python -m py_compile` for changed Python files.
5. Record the current git revision and server service status.

`python scripts/release_preflight.py` performs the backup, disposable restore,
security checks, and revision recording together. It only writes a local evidence
JSON file; it does not deploy or restart a service.

Deploy code only after those steps pass. Run schema migration before restarting
the service, then check its journal and `ops_check.py status`.

## Rollback

Stop the bot, restore the previous code revision, and restart it. Restore the
database only when a migration or data change actually requires it: first run a
restore drill on the chosen backup, preserve the current database as a separate
timestamped file, replace it with the verified copy, run `PRAGMA integrity_check`,
then start the bot. Never restore a database merely to roll back Python code.

## Monitoring

Install `deploy/sepid-ops-check.service` and `.timer`. Exit `0` means healthy;
exit `2` means a stale/missing backup, database problem, failed delivery, or a
delivery stuck for more than one hour. This produces no Telegram notification.

## Reconciliation

Run `python scripts/ops_check.py reconcile`. The UTF-8 CSV goes to `reports/`
and includes amounts, settlement markers, and inconsistent closed deals. Compare
it with the Iran panel/bank export; discrepancies must be reviewed by an admin,
never auto-approved or auto-rejected.

## Encrypted off-site backups

Mount a separately controlled remote volume, set `DEAL_OFFSITE_BACKUP_DIR`, and
set a URL-safe base64 32-byte `DEAL_BACKUP_ENCRYPTION_KEY`. Keep the key outside
the backup destination. The normal backup job will verify locally, encrypt with
AES-GCM, copy atomically, and write a SHA-256 sidecar. A failed off-site copy does
not delete or invalidate the local backup.

Generate a key once:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

The bot performs a disposable restore drill weekly. It never replaces the live
database. Review the admin health page or stored health fields for failures.

## Staging smoke test

Use a separate Telegram bot and private operator chat. Set `STAGING_BOT_TOKEN`
and `STAGING_SMOKE_CHAT_ID`, then run `python scripts/staging_smoke.py`. It sends
one silent message only to that staging chat; it never contacts production users.

For a release, manually exercise: offer acceptance, both account submissions,
buyer receipt, seller receipt, admin settlement confirmations, close, reopen,
reminder deal view, and stale-button rejection. Confirm that ordinary users get
only essential state-change messages.

The database lifecycle can also be verified without Telegram or real money:
`python scripts/synthetic_deal_e2e.py --database /path/to/synthetic.db`. The
script refuses a database named `eurobot.db`.
