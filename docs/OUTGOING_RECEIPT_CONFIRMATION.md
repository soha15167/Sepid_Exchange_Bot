# Automatic accounting for staff payout receipts

Uploading a Toman payout receipt through the admin seller-payment workflow or
the assigned reviewer-payment workflow authorizes its automatic registration
as an outgoing transaction in the Iran panel. There is no separate yes/no
approval for the site. The description remains `آگهی <advert_rowid>`, and the
panel calculates its own fees and taxes.

The receipt must contain a positive rial amount, source bank, destination bank,
recipient, transfer type and date. Incomplete or inconsistent information opens
one correction button. A complete replacement submitted through that correction
flow registers automatically. Failed/cancelled transfers, explicitly incoming
receipts, inconsistent recognized amounts, foreign-currency receipts and seller
EUR receipts are not automatically booked as outgoing rial transactions. No
missing bank, recipient, date or amount is invented.

The uploader or a full admin can process a draft. Support-only admins and other
reviewers cannot process someone else's draft. Existing yes buttons delegate to
the same automatic submission helper and retain their payload revision check;
an old no button still honors an explicit skip while the draft is unprocessed.

The durable SQLite draft is claimed with a payload compare-and-set before the
HTTP request. Concurrent callbacks cannot post it twice. Reuploads with the same
Telegram file identity within a deal reuse the same record. A successful result
is `submitted`; a failed or uncertain HTTP result is held as `unknown` for site
inspection and is never automatically retried. A process interruption during
the request can leave `submitting`, which also cannot be reposted. This is
at-most-once submission; the external panel does not provide an exactly-once
delivery guarantee.

Each uploader's receipt uses one tracked message: a media caption for an image
or document, or one text card. Status changes edit that card. Legacy separate
preview/status messages are removed after a successful card update. A new copy
is created only for an initial card or a definitively missing Telegram message;
timeouts and other uncertain edit failures preserve the current message. Old
copies are removed only after replacement delivery, and failed cleanup IDs are
retained for a later cleanup attempt. Initial/replacement sends are also tracked
durably before contacting Telegram; an uncertain delivery cannot trigger another
blind send on a later refresh.

Relevant files: `handlers/deal_outgoing.py`, `database/outgoing_receipts.py`,
and its admin/reviewer upload callers in `handlers/deal_gate.py`. No schema
migration is required.

Validation: `python -B -m unittest tests.test_deal_outgoing` uses a temporary
SQLite database and mocks all panel requests and Telegram operations. It covers
both staff roles, automatic upload/correction, duplicate uploads, concurrency,
stale buttons, exclusions, uncertain outcomes and message reuse. It never posts
a real transaction.
