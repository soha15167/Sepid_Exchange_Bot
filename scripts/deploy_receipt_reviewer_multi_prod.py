"""Upgrade production receipt reviewer to Persian, per-receipt confirmation."""
from __future__ import annotations
import shutil, sys, time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
stamp = time.strftime("%Y%m%d_%H%M%S")

def edit(rel, pairs):
    p = root / rel
    old_text = p.read_text(encoding="utf-8")
    text = old_text
    for old, new in pairs:
        if new in text:
            continue
        if text.count(old) != 1:
            raise RuntimeError(f"{rel}: anchor count={text.count(old)} for {old[:70]!r}")
        text = text.replace(old, new, 1)
    if text != old_text:
        shutil.copy2(p, p.with_name(p.name + f".pre_multi_receipt_{stamp}"))
        p.write_text(text, encoding="utf-8")
        print("updated", rel)

edit("database/db.py", [(
    '        "fee_adjusted_to_remaining",\n',
    '        "fee_adjusted_to_remaining",\n        "reviewer_received_at",\n        "reviewer_received_by",\n',
)])

old_helper = '''def _buyer_toman_received_reviewer_keyboard(offer_id: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup([[InlineKeyboardButton(\n        "✅ Received", callback_data=f"adm|tomset|{int(offer_id)}|received"\n    )]])\n\n\nasync def _notify_buyer_toman_receipt_reviewers(\n    bot, *, offer_id: int, entry_type: str, text: str = "", file_id: str = ""\n) -> None:\n    reviewer_ids = {int(x) for x in (DEAL_RECEIPT_REVIEWER_IDS or []) if int(x) > 0}\n    if not reviewer_ids:\n        return\n    body = (\n        f"{_RTL}📎 <b>Buyer payment receipt</b>\\n\\n"\n        f"{_RTL}Deal <b>{int(offer_id)}</b>\\n"\n        f"{_RTL}After verifying the payment, press <b>Received</b>. "\n        "The buyer's EUR account will then be sent to the seller."\n    )\n    markup = _buyer_toman_received_reviewer_keyboard(offer_id)\n'''
new_helper = '''def _buyer_toman_received_reviewer_keyboard(offer_id: int, receipt_index: int) -> InlineKeyboardMarkup:\n    return InlineKeyboardMarkup([[InlineKeyboardButton(\n        "✅ دریافت شد", callback_data=f"adm|tomset|{int(offer_id)}|{int(receipt_index)}"\n    )]])\n\n\nasync def _notify_buyer_toman_receipt_reviewers(\n    bot, *, offer_id: int, receipt_index: int, entry_type: str, text: str = "", file_id: str = ""\n) -> None:\n    reviewer_ids = {int(x) for x in (DEAL_RECEIPT_REVIEWER_IDS or []) if int(x) > 0}\n    if not reviewer_ids:\n        return\n    body = (\n        f"{_RTL}📎 <b>فیش واریز خریدار</b>\\n\\n"\n        f"{_RTL}معامله <b>{int(offer_id)}</b> · فیش شماره <b>{int(receipt_index) + 1}</b>\\n\\n"\n        f"{_RTL}پس از بررسی و اطمینان از دریافت وجه، دکمهٔ <b>دریافت شد</b> را بزنید.\\n"\n        f"{_RTL}اطلاعات حساب خریدار فقط پس از تأیید همهٔ فیش‌های معامله برای فروشنده ارسال می‌شود."\n    )\n    markup = _buyer_toman_received_reviewer_keyboard(offer_id, receipt_index)\n'''

review_block = '''    if is_reviewer:\n        try:\n            receipt_index = int(parts[3])\n        except (IndexError, TypeError, ValueError):\n            await q.answer("دکمهٔ فیش نامعتبر است.", show_alert=True)\n            return\n        receipt = deal_gate_update_buyer_receipt(\n            oid, receipt_index, reviewer_received_at=int(time.time()), reviewer_received_by=uid\n        )\n        if not receipt:\n            await q.answer("این فیش پیدا نشد.", show_alert=True)\n            return\n        receipts = deal_gate_buyer_receipt_list(oid)\n        pending_count = sum(1 for item in receipts if not int((item or {}).get("reviewer_received_at") or 0))\n        try:\n            await q.message.edit_reply_markup(reply_markup=None)\n        except Exception:\n            pass\n        if pending_count:\n            await q.answer(f"این فیش تأیید شد؛ {pending_count} فیش دیگر هنوز تأیید نشده است.", show_alert=True)\n            return\n'''

edit("handlers/deal_gate.py", [
    (old_helper, new_helper),
    ('    if not is_reviewer and not await _admin_sensitive_confirmation(\n', review_block + '    if not is_reviewer and not await _admin_sensitive_confirmation(\n'),
    ('await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, entry_type="text", text=text)', 'await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, receipt_index=buyer_index, entry_type="text", text=text)'),
    ('await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, entry_type=entry_type, text=cap, file_id=fid)', 'await _notify_buyer_toman_receipt_reviewers(context.bot, offer_id=oid, receipt_index=buyer_index, entry_type=entry_type, text=cap, file_id=fid)'),
])
