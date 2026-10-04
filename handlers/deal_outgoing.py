"""Explicit outgoing ledger preview for receipts uploaded by staff."""
import asyncio
import html
import hashlib
import json
import logging
import os
import re
import tempfile
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from database import outgoing_receipts as store
from database.db import deal_gate_get
from config.settings import ADMIN_IDS, DEAL_RECEIPT_REVIEWER_IDS, DEAL_SUPPORT_ADMIN_IDS, IRAN_PANEL_BASE_URL

log = logging.getLogger(__name__)
EDIT_KEY = 'deal_outgoing_edit'
_preview_locks = {}


def allowed(uid):
    return uid in (set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)) | set(DEAL_RECEIPT_REVIEWER_IDS)


def revision(row):
    return hashlib.sha256(row['payload'].encode()).hexdigest()[:10]


def problems(payload):
    required = ('bank_name', 'dest_bank', 'depositor_name', 'transfer_type', 'jdate')
    missing = [x for x in required if not str(payload.get(x) or '').strip()]
    if int(payload.get('iran_amount') or 0) <= 0:
        missing.append('iran_amount')
    if payload.get('_foreign_currency'):
        missing.append('currency')
    return missing


async def preview(bot, uid, draft_id):
    lock = _preview_locks.setdefault((uid, draft_id), asyncio.Lock())
    async with lock:
        await _preview(bot, uid, draft_id)


async def _preview(bot, uid, draft_id):
    from handlers.deal_gate import _persian_money

    row = store.get(draft_id)
    payload = json.loads(row['payload'])
    if payload.get('_receipt_kind') == 'seller_euro':
        return
    labels = {'bank_name': 'بانک مبدأ', 'dest_bank': 'بانک مقصد', 'depositor_name': 'صاحب حساب مقصد',
              'iran_amount': '💰 مبلغ', 'transfer_type': 'نوع حواله', 'jdate': 'تاریخ', 'description': 'توضیحات'}
    body = '<b>' + html.escape(str(payload.get('description') or '')) + '</b>\nثبت این فیش در خروجی سایت ایران؟\n\n' + '\n'.join(
        f'{label}: ' + (_persian_money(payload[key]) if key == 'iran_amount' and payload.get(key)
                       else html.escape(str(payload.get(key) or "—")))
        for key, label in labels.items())
    buttons = []
    if row['status'] == 'draft':
        if not problems(payload):
            buttons.append([Button('✅ بله، ثبت خروجی', callback_data=f'outreceipt|yes|{draft_id}|{revision(row)}')])
        else:
            body += '\n\nاطلاعات ناقص یا ارز غیرریالی است؛ اطلاعات خروجی ریالی را تکمیل کنید.'
        buttons.extend([[Button('✏️ اصلاح اطلاعات', callback_data=f'outreceipt|edit|{draft_id}')],
                        [Button('خیر، ثبت نشود', callback_data=f'outreceipt|no|{draft_id}')]])
    else:
        body += '\nوضعیت: ' + row['status']
    prompt = await bot.send_message(uid, body, parse_mode='HTML', reply_markup=Markup(buttons) if buttons else None)
    display = store.receipt_display(draft_id, uid)
    if display.get('file_id'):
        send = bot.send_document if display.get('type') == 'document' else bot.send_photo
        receipt = await send(uid, display['file_id'],
                             caption=f"{payload.get('description', '')} — فیش خروجی #{draft_id}",
                             reply_to_message_id=prompt.message_id)
        store.receipt_display(draft_id, uid, prompt_mid=prompt.message_id, receipt_mid=receipt.message_id, source_mid=0)
        # Never remove the original until its replacement was delivered.
        for mid in {display.get('source_mid'), display.get('receipt_mid'), display.get('prompt_mid')} - {None, 0, prompt.message_id, receipt.message_id}:
            try:
                await bot.delete_message(uid, int(mid))
            except Exception:
                log.warning('Could not remove previous receipt copy draft=%s message=%s', draft_id, mid)


async def prepare(update, context, offer_id, kind):
    try:
        await _prepare(update, context, offer_id, kind)
    except Exception:
        log.exception('Outgoing preview failed offer=%s', offer_id)
        await context.bot.send_message(update.effective_user.id, 'فیش ذخیره شد، اما آماده‌سازی خروجی ناموفق بود؛ دوباره فیش را ارسال کنید.')


async def _prepare(update, context, offer_id, kind):
    """Called after receipt storage; never posts an accounting transaction."""
    if kind == 'seller_euro':
        return
    uid = update.effective_user.id
    if not allowed(uid):
        return
    from handlers.iran_panel_sync import _read_receipt_image, _parse_payload_out
    message = update.message
    raw = message.text or message.caption or ''
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else '')
    unique = message.photo[-1].file_unique_id if message.photo else (message.document.file_unique_id if message.document else '')
    payload = None
    if file_id:
        fd, path = tempfile.mkstemp(suffix='.jpg' if message.photo else '.pdf')
        os.close(fd)
        try:
            await (await context.bot.get_file(file_id)).download_to_drive(path)
            payload, extracted, _ = await _read_receipt_image(path, 'out')
            raw = (extracted or '') + '\n' + raw
        except Exception:
            log.exception('Outgoing receipt extraction failed offer=%s', offer_id)
        finally:
            os.remove(path)
    if payload is None:
        payload, _ = _parse_payload_out(raw)
    gate = deal_gate_get(offer_id)
    payload['description'] = f"آگهی {int(gate['advert_rowid'])}"
    # A EUR number must not silently be booked as the same number of rials.
    currency = str(payload.get('_receipt_currency') or '').lower()
    payload['_foreign_currency'] = currency in {'eur', 'euro', 'usd'} or bool(re.search(r'€|\bEUR\b|یورو', raw, re.I))
    if payload['_foreign_currency']:
        payload['iran_amount'] = 0
    payload['_receipt_kind'] = kind
    key = f'{offer_id}:{unique or str(uid) + ":" + str(message.message_id)}'
    draft_id = store.create(offer_id, uid, key, payload)
    if file_id:
        store.receipt_display(draft_id, uid, file_id=file_id,
                              type='photo' if message.photo else 'document', source_mid=message.message_id)
    await preview(context.bot, uid, draft_id)
    from handlers.deal_gate import sync_deal_admin_notification
    await sync_deal_admin_notification(context.bot, offer_id, deal_complete=True)


async def callback(update, context):
    q = update.callback_query
    if not q or not allowed(q.from_user.id):
        if q:
            await q.answer('دسترسی ندارید.', show_alert=True)
        return
    try:
        parts = q.data.split('|')
        _, action, value = parts[:3]
        draft_id = int(value)
    except (ValueError, TypeError):
        return
    row = store.get(draft_id)
    if not row or (row['uploader'] != q.from_user.id and q.from_user.id not in set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)):
        await q.answer('این فیش متعلق به شما نیست.', show_alert=True)
        return
    if row['status'] != 'draft':
        await q.answer('این درخواست قبلاً پردازش شده؛ دوباره ثبت نمی‌شود.', show_alert=True)
        return
    if json.loads(row['payload']).get('_receipt_kind') == 'seller_euro':
        store.transition(draft_id, 'skipped', actor=q.from_user.id, expected=row['payload'])
        if context.user_data.get(EDIT_KEY) == draft_id:
            context.user_data.pop(EDIT_KEY, None)
        await q.answer('فیش یورو در سایت ایران ثبت نمی‌شود.')
        await q.message.edit_text('فیش یورو — نیازی به ثبت در سایت ایران نیست.')
        return
    if action == 'edit':
        context.user_data[EDIT_KEY] = draft_id
        await q.answer()
        await context.bot.send_message(q.from_user.id, 'اطلاعات کامل را به صورت زیر بفرستید (مبلغ ریال):\nبانک: ملت\nمقصد: ملی\nنام گیرنده: ...\nمبلغ: ...\nنوع: پایا\nتاریخ: 1405/06/17\nبرای خروج بنویسید: انصراف')
        return
    payload = json.loads(row['payload'])
    if action not in {'yes', 'no'}:
        return
    if action == 'yes' and problems(payload):
        await q.answer('ابتدا اطلاعات را کامل کنید.', show_alert=True)
        return
    if action == 'yes' and (len(parts) != 4 or parts[3] != revision(row)):
        await q.answer('اطلاعات تغییر کرده؛ پیش‌نمایش جدید را تأیید کنید.', show_alert=True)
        await preview(context.bot, q.from_user.id, draft_id)
        return
    if not store.transition(draft_id, 'submitting' if action == 'yes' else 'skipped', actor=q.from_user.id, expected=row['payload']):
        await q.answer('این درخواست در حال پردازش است.', show_alert=True)
        return
    await q.answer('در حال ثبت…' if action == 'yes' else 'ثبت خروجی انجام نشد.')
    if action == 'yes':
        from handlers.iran_panel_sync import _panel_payload_for_submit
        from utils.iran_panel_client import post_transaction
        try:
            ok, error = await asyncio.to_thread(post_transaction, base_url=IRAN_PANEL_BASE_URL, payload=_panel_payload_for_submit(payload, 'out'))
        except Exception as exc:
            ok, error = False, type(exc).__name__
        store.finish(draft_id, ok, error)
        result = '✅ خروجی در سایت ثبت شد.' if ok else 'نتیجه ثبت نامشخص است؛ ادمین باید سایت را بررسی کند. برای جلوگیری از ثبت تکراری، دوباره ارسال نشد.'
    else:
        result = 'ثبت خروجی برای این فیش انجام نشد.'
    display = store.receipt_display(draft_id, q.from_user.id)
    if display.get('file_id'):
        try:
            send = context.bot.send_document if display.get('type') == 'document' else context.bot.send_photo
            receipt = await send(q.from_user.id, display['file_id'],
                caption=f"{payload.get('description', '')} — فیش خروجی #{draft_id}")
            status = await context.bot.send_message(q.from_user.id, result, reply_to_message_id=receipt.message_id)
            store.receipt_display(draft_id, q.from_user.id, receipt_mid=receipt.message_id, prompt_mid=status.message_id)
            for mid in {display.get('receipt_mid'), display.get('prompt_mid'), q.message.message_id} - {None, 0, receipt.message_id, status.message_id}:
                try:
                    await context.bot.delete_message(q.from_user.id, mid)
                except Exception:
                    log.warning('Could not remove old outgoing preview draft=%s message=%s', draft_id, mid)
        except Exception:
            log.exception('Outgoing result display failed draft=%s', draft_id)
            await q.message.edit_text(result)
    else:
        await q.message.edit_text(result)
    from handlers.deal_gate import sync_deal_admin_notification
    await sync_deal_admin_notification(context.bot, row['offer_id'], deal_complete=True)


async def edit_message(update, context):
    draft_id = context.user_data.get(EDIT_KEY)
    if not draft_id or not update.message or not allowed(update.effective_user.id):
        return False
    text = update.message.text or ''
    if text.strip() in {'/cancel', 'انصراف'}:
        context.user_data.pop(EDIT_KEY, None)
        await update.message.reply_text('ویرایش متوقف شد.')
        return True
    row = store.get(draft_id)
    uid = update.effective_user.id
    if not row or row['status'] != 'draft' or (row['uploader'] != uid and uid not in set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)):
        context.user_data.pop(EDIT_KEY, None)
        return True
    from handlers.iran_panel_sync import _parse_payload_out
    original = json.loads(row['payload'])
    if original.get('_receipt_kind') == 'seller_euro':
        context.user_data.pop(EDIT_KEY, None)
        await update.message.reply_text('فیش یورو در سایت ایران ثبت نمی‌شود.')
        return True
    payload, _ = _parse_payload_out(text)
    payload['_receipt_kind'] = original.get('_receipt_kind')
    payload['_foreign_currency'] = bool(re.search(r'€|\bEUR\b|یورو', text, re.I))
    payload['description'] = json.loads(row['payload'])['description']
    store.transition(draft_id, 'draft', actor=uid, payload=payload)
    context.user_data.pop(EDIT_KEY, None)
    await preview(context.bot, uid, draft_id)
    return True
