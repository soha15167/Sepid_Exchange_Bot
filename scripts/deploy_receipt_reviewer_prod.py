"""Apply the narrowly scoped receipt-reviewer feature to a production tree."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
STAMP = time.strftime("%Y%m%d_%H%M%S")


def edit(relative: str, replacements: list[tuple[str, str]]) -> None:
    path = ROOT / relative
    original = path.read_text(encoding="utf-8")
    updated = original
    for old, new in replacements:
        if new in updated:
            continue
        count = updated.count(old)
        if count != 1:
            raise RuntimeError(f"{relative}: expected one anchor, found {count}: {old[:80]!r}")
        updated = updated.replace(old, new, 1)
    if updated != original:
        backup = path.with_name(path.name + f".pre_receipt_reviewer_{STAMP}")
        shutil.copy2(path, backup)
        path.write_text(updated, encoding="utf-8")
        print(f"updated {relative}; backup={backup.name}")


edit("config/settings.py", [(
    'ADMIN_NOTIFY_CHAT_IDS = _env_int_id_list(\n    "DEAL_ADMIN_NOTIFY_CHAT_ID", "ADMIN_NOTIFY_CHAT_ID"\n)\n',
    'ADMIN_NOTIFY_CHAT_IDS = _env_int_id_list(\n    "DEAL_ADMIN_NOTIFY_CHAT_ID", "ADMIN_NOTIFY_CHAT_ID"\n)\nDEAL_RECEIPT_REVIEWER_IDS = _env_int_id_list("DEAL_RECEIPT_REVIEWER_ID")\n',
)])

edit("handlers/access_gate.py", [
    ('from config.settings import ADMIN_IDS\n', 'from config.settings import ADMIN_IDS, DEAL_RECEIPT_REVIEWER_IDS\n'),
    (
        '    if u.id in set(ADMIN_IDS or []):\n        return\n    if get_user(u.id) is not None:\n',
        '    if u.id in set(ADMIN_IDS or []):\n        return\n    if (\n        u.id in set(DEAL_RECEIPT_REVIEWER_IDS or [])\n        and update.callback_query\n        and normalize_telegram_callback_data(update.callback_query.data or "").startswith("adm|tomset|")\n    ):\n        return\n    if get_user(u.id) is not None:\n',
    ),
])

helper = '''\n\ndef _buyer_toman_received_reviewer_keyboard(offer_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup([[InlineKeyboardButton(\n        "✅ Received", callback_data=f"adm|tomset|{int(offer_id)}|received"\n    )]])\n\n\nasync def _notify_buyer_toman_receipt_reviewers(\n    bot, *, offer_id: int, entry_type: str, text: str = "", file_id: str = ""\n) -> None:\n    reviewer_ids = {int(x) for x in (DEAL_RECEIPT_REVIEWER_IDS or []) if int(x) > 0}\n    if not reviewer_ids:\n        return\n    body = (\n        f"{_RTL}📎 <b>Buyer payment receipt</b>\\n\\n"\n        f"{_RTL}Deal <b>{int(offer_id)}</b>\\n"\n        f"{_RTL}After verifying the payment, press <b>Received</b>. "\n        "The buyer's EUR account will then be sent to the seller."\n    )\n    markup = _buyer_toman_received_reviewer_keyboard(offer_id)\n    for reviewer_id in reviewer_ids:\n        try:\n            if entry_type == "document" and file_id:\n                await bot.send_document(reviewer_id, file_id, caption=body, parse_mode=ParseMode.HTML, reply_markup=markup)\n            elif entry_type == "photo" and file_id:\n                await bot.send_photo(reviewer_id, file_id, caption=body, parse_mode=ParseMode.HTML, reply_markup=markup)\n            else:\n                message = body + (f"\\n\\n<pre>{html_module.escape(text.strip()[:2000])}</pre>" if text.strip() else "")\n                await bot.send_message(reviewer_id, message, parse_mode=ParseMode.HTML, reply_markup=markup)\n        except Exception as exc:\n            logger.warning("deal_buyer_receipt reviewer notify failed reviewer=%s offer=%s: %s", reviewer_id, offer_id, exc)\n'''

edit("handlers/deal_gate.py", [
    ('    BANK_CARDS,\n    DEAL_SUPPORT_ADMIN_IDS,\n', '    BANK_CARDS,\n    DEAL_RECEIPT_REVIEWER_IDS,\n    DEAL_SUPPORT_ADMIN_IDS,\n'),
    ('\ndef _deal_gate_allows_party_receipts(', helper + '\n\ndef _deal_gate_allows_party_receipts('),
    (
        '    if not await _require_full_deal_admin(q):\n        return\n    parts = (q.data or "").split("|")\n    if len(parts) not in (3, 4) or parts[0] != "adm" or parts[1] != "tomset":\n        return\n    try:\n        oid = int(parts[2])\n    except (TypeError, ValueError):\n        return\n    gate = deal_gate_get(oid)\n',
        '    parts = (q.data or "").split("|")\n    if len(parts) not in (3, 4) or parts[0] != "adm" or parts[1] != "tomset":\n        return\n    try:\n        oid = int(parts[2])\n    except (TypeError, ValueError):\n        return\n    uid = int(q.from_user.id)\n    is_reviewer = uid in set(DEAL_RECEIPT_REVIEWER_IDS or [])\n    if not _is_full_deal_admin(uid) and not is_reviewer:\n        await _require_full_deal_admin(q)\n        return\n    gate = deal_gate_get(oid)\n',
    ),
    ('    if not await _admin_sensitive_confirmation(\n        context,\n        q,\n        action="buyer_toman_settled",\n', '    if not is_reviewer and not await _admin_sensitive_confirmation(\n        context,\n        q,\n        action="buyer_toman_settled",\n'),
    (
        '    _log(oid, f"فیش واریز متنی خریدار ({len(text)} کاراکتر)", from_role="buyer")\n    await sync_deal_admin_notification(',
        '    _log(oid, f"فیش واریز متنی خریدار ({len(text)} کاراکتر)", from_role="buyer")\n    await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, entry_type="text", text=text)\n    await sync_deal_admin_notification(',
    ),
    (
        '    _log(oid, f"فیش واریز {entry_type} خریدار", from_role="buyer")\n    await sync_deal_admin_notification(',
        '    _log(oid, f"فیش واریز {entry_type} خریدار", from_role="buyer")\n    await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, entry_type=entry_type, text=cap, file_id=fid)\n    await sync_deal_admin_notification(',
    ),
])

env_path = ROOT / ".env"
env_text = env_path.read_text(encoding="utf-8")
line = "DEAL_RECEIPT_REVIEWER_ID=139549321"
if line not in env_text.splitlines():
    backup = env_path.with_name(env_path.name + f".pre_receipt_reviewer_{STAMP}")
    shutil.copy2(env_path, backup)
    env_path.write_text(env_text.rstrip() + "\n" + line + "\n", encoding="utf-8")
    print(f"updated .env; backup={backup.name}")
