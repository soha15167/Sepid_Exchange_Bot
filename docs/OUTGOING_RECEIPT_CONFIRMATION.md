# Receipt outgoing accounting

Staff seller-EUR proxy uploads, admin Toman payouts to the EUR seller, and
claimed reviewer payout receipts now open a separate outgoing preview. Each
preview includes the receipt's source bank, destination bank, recipient, rial
amount, transfer type and date. Description is always `آگهی <advert_rowid>`.

Yes submits through the existing `out` payload adapter. No durably skips the
draft. Edit accepts a complete replacement set of receipt fields and presents
a new preview. A true EUR receipt requires explicit rial details; its numeric
EUR value is never silently recorded as rials. Nothing is posted on upload.

The uploader or a full admin can confirm a draft. Database compare-and-set
claims and preview revision checks prevent concurrent and stale confirmations.
Reuploads with the same Telegram file identity in a deal reuse the same draft.
An uncertain network result is held as `unknown` for panel inspection rather
than automatically resubmitted. This is at-most-once submission, not a promise
of exactly-once delivery across the external HTTP service.

The `deal_outgoing_drafts` table is created on first access. Draft state survives
bot restarts. The admin deal message displays counts by outgoing status.

Validation: `python -m unittest tests.test_deal_outgoing
tests.test_admin_seller_toman_settlement tests.test_main_registration` (12 tests).
The existing reviewer suite has three failures reproduced against the downloaded
pre-change live handler; these are unrelated to this change.

Deployment files: `database/outgoing_receipts.py`, `handlers/deal_outgoing.py`,
`handlers/deal_gate.py`, `handlers/access_gate.py`, `main.py`. Preserve the live
reviewer amount-edit access exceptions. Compare current server hashes with the
downloaded copies before installation, back up files and database, validate
imports, then restart `telegram-bot.service`.

Deployed after explicit approval on 2026-09-08. All 12 focused tests passed in
server staging. Release preflight (backup, restore drill, security) passed.
Production hashes match the tested files; application and scheduler started,
service active/running with zero automatic restarts. Code backup:
`/root/sepid-outgoing-backup-a0Weyv`. No real outgoing transaction was created
as part of deployment validation.
