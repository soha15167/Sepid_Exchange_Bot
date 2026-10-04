"""Shared admin/reviewer amount correction, with stale-edit protection."""
import asyncio
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from config.settings import ADMIN_IDS, DEAL_SUPPORT_ADMIN_IDS, DEAL_RECEIPT_REVIEWER_IDS
from database.db import deal_gate_buyer_receipt_list, deal_gate_get
from database.receipt_management import fingerprint, edit_buyer_amount

KEY = 'deal_rcpt_admin_edit_pending'


def allowed(uid):
    return uid in (set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)) | set(DEAL_RECEIPT_REVIEWER_IDS)


async def callback(update, context):
    q = update.callback_query
    if not allowed(q.from_user.id):
        await q.answer('دسترسی ندارید.', show_alert=True)
        return
    parts = q.data.split('|')
    if len(parts) == 3 and parts[2] == 'cancel':
        context.user_data.pop(KEY, None)
        await q.answer('ویرایش لغو شد')
        await q.message.delete()
        return
    try:
        oid, index = int(parts[2]), int(parts[3])
        items = deal_gate_buyer_receipt_list(oid)
        if index < 0:
            raise ValueError()
        item = items[index]
        gate = deal_gate_get(oid)
        if not gate or int(gate.get('buyer_toman_settled_at') or 0) > 0 or item.get('attachment_removed_at') or item.get('accounting_status') in {'submitted','submitting','panel_failed','duplicate','rejected'}:
            raise ValueError()
    except (IndexError, ValueError):
        await q.answer('فیش حذف/ثبت شده یا نیازمند بررسی حسابداری است؛ ویرایش مستقیم مجاز نیست.', show_alert=True)
        return
    context.user_data[KEY] = dict(offer_id=oid, index=index, expected=fingerprint(item), actor_id=q.from_user.id, reviewer_amount_edit=True)
    await q.answer()
    await context.bot.send_message(q.from_user.id, f"آگهی {gate['advert_rowid']} — فیش {index+1}\nمبلغ صحیح را فقط به ریال بفرستید. پس از ویرایش، دریافت وجه باید دوباره تأیید شود.", reply_markup=Markup([[Button('انصراف', callback_data='adm|tomamt|cancel')]]))


async def message(update, context):
    pending = context.user_data.get(KEY)
    if not pending or not pending.get('reviewer_amount_edit') or not update.message:
        return False
    uid = update.effective_user.id
    if not allowed(uid) or uid != pending['actor_id']:
        return False
    text = (update.message.text or '').strip()
    if text in {'انصراف', '/cancel'}:
        context.user_data.pop(KEY, None)
        await update.message.reply_text('ویرایش لغو شد.')
        return True
    clean = text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).replace(',', '').replace('٬', '').replace(' ', '')
    if not clean.isascii() or not clean.isdigit() or not 0 < int(clean) < 10**16:
        await update.message.reply_text('فقط عدد مثبت به ریال بفرستید؛ برای خروج «انصراف».')
        return True
    from handlers import deal_gate as flow
    oid, index = pending['offer_id'], pending['index']
    async with flow._buyer_toman_settlement_locks.setdefault(oid, asyncio.Lock()):
        item = edit_buyer_amount(oid, index, pending['expected'], int(clean), uid)
        context.user_data.pop(KEY, None)
        if not item:
            await update.message.reply_text('فیش تغییر کرده یا ثبت شده است؛ دوباره از دکمه ویرایش شروع کنید.')
            return True
        await flow._sync_buyer_receipt_reviewer_messages(context.bot, offer_id=oid, receipt_index=index, receipt=item)
        await flow.sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await update.message.reply_text('✅ مبلغ اصلاح شد؛ دریافت وجه را دوباره بررسی و تأیید کنید.')
    return True
