"""Build a narrow production patch while preserving production-only OCR tools."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


snapshot = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).resolve()
output = Path(sys.argv[3]).resolve()
output.mkdir(parents=True, exist_ok=True)


def section(text: str, start: str, end: str) -> str:
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def replace_section(target: str, source_text: str, start: str, end: str) -> str:
    left = target.index(start)
    right = target.index(end, left)
    return target[:left] + section(source_text, start, end) + target[right:]


prod_gate = (snapshot / "deal_gate.py").read_text(encoding="utf-8")
local_gate = (source / "handlers" / "deal_gate.py").read_text(encoding="utf-8")

if "    deal_gate_confirm_buyer_receipt_received,\n" not in prod_gate:
    prod_gate = prod_gate.replace(
        "    deal_gate_buyer_receipt_list,\n",
        "    deal_gate_buyer_receipt_list,\n"
        "    deal_gate_confirm_buyer_receipt_received,\n",
        1,
    )
if "_buyer_toman_settlement_locks:" not in prod_gate:
    prod_gate = prod_gate.replace(
        "_admin_sync_locks: dict[int, asyncio.Lock] = {}\n",
        "_admin_sync_locks: dict[int, asyncio.Lock] = {}\n"
        "_buyer_toman_settlement_locks: dict[int, asyncio.Lock] = {}\n",
        1,
    )

for start, end in (
    ("def _buyer_toman_received_reviewer_keyboard(", "def _deal_gate_allows_party_receipts("),
    ("async def _deal_admin_proxy_receipt_try_message(", "async def _deal_admin_proxy_receipt_try_photo("),
    ("async def _deal_admin_proxy_receipt_try_photo(", "def _buyer_toman_deposit_message_html("),
    ("async def deal_admin_toman_settled_callback(", "async def deal_admin_send_buyer_eur_account_callback("),
):
    prod_gate = replace_section(prod_gate, local_gate, start, end)

# The upgraded notifier also receives the gate so it can render the public deal number.
prod_gate = prod_gate.replace(
    "context.bot, offer_id=oid, receipt_index=buyer_index, entry_type=\"text\", text=text",
    "context.bot, offer_id=oid, gate=gate, receipt_index=buyer_index, entry_type=\"text\", text=text",
)
prod_gate = prod_gate.replace(
    "context.bot, offer_id=oid, receipt_index=buyer_index, entry_type=entry_type, text=cap, file_id=fid",
    "context.bot, offer_id=oid, gate=gate, receipt_index=buyer_index, entry_type=entry_type, text=cap, file_id=fid",
)

old_total = '''        submitted_total = sum(
            int(item.get("amount_rial") or 0)
            for index, item in enumerate(deal_gate_buyer_receipt_list(oid))
            if index != receipt_index
            and (item.get("accounting_status") or "") == "submitted"
        )
        remaining_before = max(0, expected_rial - submitted_total)
'''
new_total = '''        recognized_total = sum(
            int(item.get("amount_rial") or 0)
            for index, item in enumerate(deal_gate_buyer_receipt_list(oid))
            if index != receipt_index
            and (item.get("accounting_status") or "").strip().lower()
            in {"review", "ready_for_review", "submitting", "submitted", "panel_failed"}
        )
        remaining_before = max(0, expected_rial - recognized_total)
'''
if old_total not in prod_gate:
    raise RuntimeError("production auto-account total anchor changed")
prod_gate = prod_gate.replace(old_total, new_total, 1)
prod_gate = prod_gate.replace("cumulative = submitted_total + amount_rial", "cumulative = recognized_total + amount_rial", 1)
prod_gate = prod_gate.replace(
    'in ("submitted", "submitting", "processing", "ready_for_review")',
    'in ("review", "ready_for_review", "submitting", "submitted", "panel_failed")',
    1,
)

sync_anchor = '''        try:
            await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
'''
sync_upgrade = '''        try:
            latest_receipts = deal_gate_buyer_receipt_list(oid)
            if 0 <= int(receipt_index) < len(latest_receipts):
                await _sync_buyer_receipt_reviewer_messages(
                    context.bot,
                    offer_id=oid,
                    receipt_index=receipt_index,
                    receipt=latest_receipts[receipt_index],
                )
        except Exception:
            logger.exception("deal_receipt_accounting: reviewer sync failed offer=%s", oid)
        try:
            await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
'''
auto_start = prod_gate.index("async def _auto_account_buyer_receipt(")
auto_end = prod_gate.index("async def _send_deal_receipt_review_preview(", auto_start)
auto_block = prod_gate[auto_start:auto_end]
if sync_anchor not in auto_block:
    raise RuntimeError("production auto-account sync anchor changed")
auto_block = auto_block.replace(sync_anchor, sync_upgrade, 1)
prod_gate = prod_gate[:auto_start] + auto_block + prod_gate[auto_end:]
prod_gate = prod_gate.replace(
    '        fields.update(amount_rial=amount, recognized_amount_rial=amount)\n',
    '''        expected_rial = _buyer_expected_rial(gate)
        fields.update(
            amount_rial=amount,
            recognized_amount_rial=amount,
            expected_rial=expected_rial,
            reviewer_received_at=0,
            reviewer_received_by=0,
        )
''',
    1,
)
prod_gate = prod_gate.replace(
    '''    if not updated:
        await update.message.reply_text("ویرایش ذخیره نشد؛ دوباره تلاش کنید.")
        return True
    await _close_deal_receipt_previews(context.bot, old)
''',
    '''    if not updated:
        await update.message.reply_text("ویرایش ذخیره نشد؛ دوباره تلاش کنید.")
        return True
    if field == "amt":
        expected_rial = _buyer_expected_rial(gate)
        running_rial = 0
        for item_index, item in enumerate(deal_gate_buyer_receipt_list(oid)):
            if (item.get("accounting_status") or "").strip().lower() in {"duplicate", "rejected"}:
                continue
            running_rial += max(0, int(item.get("amount_rial") or 0))
            recalculated = deal_gate_update_buyer_receipt(
                oid,
                item_index,
                expected_rial=expected_rial,
                cumulative_rial=running_rial,
                remaining_rial=max(0, expected_rial - running_rial),
            )
            if item_index == idx and recalculated:
                updated = recalculated
    await _close_deal_receipt_previews(context.bot, old)
''',
    1,
)
prod_gate = prod_gate.replace(
    '''    await _send_deal_receipt_review_preview(
        context.bot, gate=gate, receipt_index=idx, receipt=updated
    )
    await update.message.reply_text("✅ فیلد ویرایش شد و پیش‌نمایش جدید ساخته شد.")
''',
    '''    await _send_deal_receipt_review_preview(
        context.bot, gate=gate, receipt_index=idx, receipt=updated
    )
    await _sync_buyer_receipt_reviewer_messages(
        context.bot, offer_id=oid, receipt_index=idx, receipt=updated
    )
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await update.message.reply_text("✅ فیلد ویرایش شد و پیش‌نمایش جدید ساخته شد.")
''',
    1,
)
prod_gate = prod_gate.replace(
    '''            [
                InlineKeyboardButton(
                    "✏️ ویرایش فیلدها",
                    callback_data=f"adm|rcptedit|{oid}|{int(receipt_index)}",
                )
            ],
''',
    '''            [
                InlineKeyboardButton(
                    "✏️ ویرایش مبلغ",
                    callback_data=f"adm|rcptfld|{oid}|{int(receipt_index)}|amt",
                )
            ],
            [
                InlineKeyboardButton(
                    "🛠 ویرایش سایر فیلدها",
                    callback_data=f"adm|rcptedit|{oid}|{int(receipt_index)}",
                )
            ],
''',
    1,
)
(output / "deal_gate.py").write_text(prod_gate, encoding="utf-8", newline="\n")


prod_db = (snapshot / "db.py").read_text(encoding="utf-8")
local_db = (source / "database" / "db.py").read_text(encoding="utf-8")
if '        "reviewer_notify_mids",\n' not in prod_db:
    prod_db = prod_db.replace(
        '        "reviewer_received_by",\n',
        '        "reviewer_received_by",\n        "reviewer_notify_mids",\n',
        1,
    )
if "def deal_gate_confirm_buyer_receipt_received(" not in prod_db:
    helper = section(
        local_db,
        "def deal_gate_confirm_buyer_receipt_received(",
        "def deal_gate_claim_buyer_receipt_submission(",
    )
    marker = "def deal_gate_has_submitted_buyer_receipt("
    prod_db = prod_db.replace(marker, helper + marker, 1)
(output / "db.py").write_text(prod_db, encoding="utf-8", newline="\n")


prod_offers = (snapshot / "offers.py").read_text(encoding="utf-8")
local_offers = (source / "handlers" / "offers.py").read_text(encoding="utf-8")
mixed_start = "    if slides_mode and photo_items and text_items:\n"
mixed_end = "    if not items:\n"
if mixed_start in prod_offers:
    left = prod_offers.index(mixed_start, prod_offers.index("def _buyer_toman_receipt_admin_line_html("))
    right = prod_offers.index(mixed_end, left)
    prod_offers = prod_offers[:left] + prod_offers[right:]
status_block = section(
    local_offers,
    "    active_review_entries = [\n",
    "    if int(gate.get(\"buyer_toman_settled_at\") or 0) > 0:\n",
)
offers_start = prod_offers.index("def _buyer_toman_receipt_admin_line_html(")
settled = prod_offers.index(
    "    if int(gate.get(\"buyer_toman_settled_at\") or 0) > 0:\n", offers_start
)
if "    active_review_entries = [\n" not in prod_offers[offers_start:settled]:
    prod_offers = prod_offers[:settled] + status_block + prod_offers[settled:]
(output / "offers.py").write_text(prod_offers, encoding="utf-8", newline="\n")

for name in ("deal_gate.py", "db.py", "offers.py"):
    print(output / name)
