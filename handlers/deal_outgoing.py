"""Automatically book staff Toman payouts, with one durable receipt card."""
import asyncio
import html
import hashlib
import json
import logging
import os
import re
import tempfile
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.error import BadRequest
from database import outgoing_receipts as store
from database.db import deal_gate_get
from config.settings import ADMIN_IDS, DEAL_RECEIPT_REVIEWER_IDS, DEAL_SUPPORT_ADMIN_IDS, IRAN_PANEL_BASE_URL

log = logging.getLogger(__name__)
EDIT_KEY = 'deal_outgoing_edit'
_preview_locks = {}


def allowed(uid):
    return uid in (set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)) | set(DEAL_RECEIPT_REVIEWER_IDS)


def _can_manage(row, uid):
    return bool(row and allowed(uid) and (
        row['uploader'] == uid or uid in set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)
    ))


def revision(row):
    return hashlib.sha256(row['payload'].encode()).hexdigest()[:10]


def problems(payload):
    required = ('bank_name', 'dest_bank', 'depositor_name', 'transfer_type', 'jdate')
    missing = [x for x in required if not str(payload.get(x) or '').strip()]
    try:
        amount = int(payload.get('iran_amount') or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        missing.append('iran_amount')
    if payload.get('_foreign_currency'):
        missing.append('currency')
    if payload.get('_invalid_receipt'):
        missing.append('receipt')
    if payload.get('_amount_inconsistent'):
        missing.append('amount_review')
    return missing


def _receipt_validation(payload, raw):
    from handlers.iran_panel_sync import _receipt_failed, _receipt_direction_from_text

    direction = str(payload.get('_detected_direction') or '').strip().lower()
    if direction not in {'in', 'out'}:
        direction = _receipt_direction_from_text(raw)
    payload['_foreign_currency'] = bool(payload.get('_foreign_currency')) or str(
        payload.get('_receipt_currency') or '',
    ).strip().lower() in {'eur', 'euro', 'usd'}
    payload['_invalid_receipt'] = bool(payload.get('_invalid_receipt')) or direction == 'in' or _receipt_failed(
        raw, str(payload.get('_receipt_status') or ''),
    )
    disagreement = payload.get('_amount_disagreement')
    payload['_amount_inconsistent'] = bool(payload.get('_amount_inconsistent')) or bool(disagreement)
    return payload


async def auto_submit(uid, draft_id, *, expected_revision=None):
    """Atomically submit a complete authorized payout once; never retry unknown."""
    row = store.get(draft_id)
    if not _can_manage(row, uid):
        return 'unauthorized'
    if row['status'] != 'draft':
        return row['status']
    if expected_revision is not None and expected_revision != revision(row):
        return 'stale'
    payload = _receipt_validation(json.loads(row['payload']), '')
    if payload.get('_receipt_kind') == 'seller_euro':
        store.transition(draft_id, 'skipped', actor=uid, expected=row['payload'])
        return 'skipped'
    if problems(payload):
        return 'draft'
    if not store.transition(draft_id, 'submitting', actor=uid, expected=row['payload']):
        return (store.get(draft_id) or {}).get('status', 'draft')
    try:
        from handlers.iran_panel_sync import _panel_payload_for_submit
        from utils.iran_panel_client import post_transaction
        ok, error = await asyncio.to_thread(
            post_transaction, base_url=IRAN_PANEL_BASE_URL,
            payload=_panel_payload_for_submit(payload, 'out'),
        )
    except Exception as exc:
        log.exception('Outgoing submission outcome unknown draft=%s', draft_id)
        ok, error = False, type(exc).__name__
    store.finish(draft_id, ok, error)
    return 'submitted' if ok else 'unknown'


def _message_missing(exc):
    return isinstance(exc, BadRequest) and any(part in str(exc).lower() for part in (
        'message to edit not found', 'message to delete not found', 'message_id_invalid',
    ))


def _message_unchanged(exc):
    return isinstance(exc, BadRequest) and 'message is not modified' in str(exc).lower()


def _card(row):
    from handlers.deal_gate import _persian_money

    payload = _receipt_validation(json.loads(row['payload']), '')
    labels = {'bank_name': 'بانک مبدأ', 'dest_bank': 'بانک مقصد', 'depositor_name': 'صاحب حساب مقصد',
              'iran_amount': '💰 مبلغ', 'transfer_type': 'نوع حواله', 'jdate': 'تاریخ'}
    statuses = {
        'draft': 'اطلاعات این فیش را تکمیل کنید؛ پس از تکمیل، خودکار در سایت ثبت می‌شود.',
        'submitting': '⏳ در حال ثبت خودکار خروجی در سایت…',
        'submitted': '✅ خروجی به‌صورت خودکار در سایت ثبت شد.',
        'unknown': 'نتیجهٔ ثبت نامشخص است؛ ادمین باید سایت را بررسی کند. برای جلوگیری از ثبت تکراری، دوباره ارسال نشد.',
        'skipped': 'ثبت خروجی برای این فیش انجام نشد.',
    }
    # Keep media captions within Telegram's limit without truncating HTML tags.
    body = '<b>' + html.escape(str(payload.get('description') or '')[:80]) + '</b>\n'
    body += statuses.get(row['status'], 'وضعیت ثبت: ' + row['status']) + '\n\n'
    body += '\n'.join(
        f'{label}: ' + (_persian_money(int(payload[key])) if key == 'iran_amount' and str(payload.get(key) or '').isdigit()
                       else html.escape(str(payload.get(key) or '—')[:70]))
        for key, label in labels.items()
    )
    buttons = []
    if row['status'] == 'draft':
        if payload.get('_foreign_currency'):
            body += '\n\nمبلغ ارزی در سایت ریالی ثبت نمی‌شود؛ اطلاعات خروجی ریالی را وارد کنید.'
        if payload.get('_invalid_receipt'):
            body += '\n\nرسید ناموفق یا ورودی است؛ اطلاعات پرداخت موفق خروجی را اصلاح کنید.'
        if payload.get('_amount_inconsistent'):
            body += '\n\nمبلغ استخراج‌شده ناسازگار است؛ مبلغ صحیح را در اطلاعات فیش وارد کنید.'
        buttons = [[Button('✏️ تکمیل / اصلاح اطلاعات', callback_data=f"outreceipt|edit|{row['id']}")]]
    return body, Markup(buttons) if buttons else None


async def _cleanup_display(bot, uid, draft_id, display, keep_mid):
    pending = set(display.get('cleanup_mids') or [])
    pending.update(display.get(key) for key in ('source_mid', 'prompt_mid', 'receipt_mid'))
    pending -= {None, 0, keep_mid}
    failed = []
    for mid in pending:
        try:
            await bot.delete_message(chat_id=uid, message_id=int(mid))
        except Exception as exc:
            if not _message_missing(exc):
                failed.append(int(mid))
                log.warning('Could not remove old outgoing card draft=%s message=%s', draft_id, mid)
    store.receipt_display(draft_id, uid, cleanup_mids=sorted(failed))


async def preview(bot, uid, draft_id):
    lock = _preview_locks.setdefault((uid, draft_id), asyncio.Lock())
    async with lock:
        await _preview(bot, uid, draft_id)


async def _preview(bot, uid, draft_id):
    """Edit the receipt card; transient edit failures never create another copy."""
    row = store.get(draft_id)
    if not row or json.loads(row['payload']).get('_receipt_kind') == 'seller_euro':
        return
    body, markup = _card(row)
    display = store.receipt_display(draft_id, uid)
    is_media = bool(display.get('file_id'))
    existing_mid = display.get('receipt_mid') if is_media else display.get('prompt_mid')
    if display.get('delivery_status') in {'sending', 'unknown'}:
        log.warning('Outgoing card delivery uncertain; did not create a duplicate draft=%s', draft_id)
        return
    card_mid = existing_mid
    if existing_mid:
        try:
            if is_media:
                await bot.edit_message_caption(chat_id=uid, message_id=int(existing_mid), caption=body,
                                               parse_mode='HTML', reply_markup=markup)
            else:
                await bot.edit_message_text(chat_id=uid, message_id=int(existing_mid), text=body,
                                            parse_mode='HTML', reply_markup=markup)
        except Exception as exc:
            if _message_missing(exc):
                card_mid = None
            elif not _message_unchanged(exc):
                log.warning('Outgoing card edit failed; preserved existing card draft=%s', draft_id)
                return
    if not card_mid:
        store.receipt_display(draft_id, uid, delivery_status='sending')
        try:
            if is_media:
                send = bot.send_document if display.get('type') == 'document' else bot.send_photo
                message = await send(uid, display['file_id'], caption=body,
                                     parse_mode='HTML', reply_markup=markup)
            else:
                message = await bot.send_message(uid, body, parse_mode='HTML', reply_markup=markup)
        except Exception:
            store.receipt_display(draft_id, uid, delivery_status='unknown')
            raise
        card_mid = int(message.message_id)
    # Persist the new destination and cleanup list before touching old copies.
    obsolete = set(display.get('cleanup_mids') or [])
    obsolete.update(display.get(key) for key in ('source_mid', 'prompt_mid', 'receipt_mid'))
    obsolete -= {None, 0, card_mid}
    store.receipt_display(draft_id, uid, receipt_mid=card_mid if is_media else 0,
                          prompt_mid=0 if is_media else card_mid, source_mid=0,
                          cleanup_mids=sorted(obsolete), delivery_status='delivered')
    await _cleanup_display(bot, uid, draft_id, display, card_mid)


async def _refresh_deal(context, offer_id):
    from handlers.deal_gate import sync_deal_admin_notification
    try:
        await sync_deal_admin_notification(context.bot, offer_id, deal_complete=True)
    except Exception:
        log.exception('Outgoing admin card refresh failed offer=%s', offer_id)


async def _track_source(uid, draft_id, message, file_id):
    lock = _preview_locks.setdefault((uid, draft_id), asyncio.Lock())
    async with lock:
        display = store.receipt_display(draft_id, uid)
        pending = set(display.get('cleanup_mids') or [])
        if display.get('source_mid'):
            pending.add(int(display['source_mid']))
        store.receipt_display(draft_id, uid, file_id=file_id,
                              type='photo' if message.photo else 'document',
                              source_mid=message.message_id, cleanup_mids=sorted(pending))


async def prepare(update, context, offer_id, kind):
    try:
        await _prepare(update, context, offer_id, kind)
    except Exception:
        log.exception('Outgoing processing failed offer=%s', offer_id)
        await context.bot.send_message(update.effective_user.id,
            'فیش محفوظ است، اما تکمیل ثبت یا نمایش وضعیت ناموفق بود؛ وضعیت ثبت را بررسی کنید.')


async def _prepare(update, context, offer_id, kind):
    """A staff Toman payout upload is the accounting authorization."""
    if kind not in {'seller_toman', 'viewer_toman'}:
        return
    uid = update.effective_user.id
    if not allowed(uid):
        return
    from handlers.iran_panel_sync import _read_receipt_image, _parse_payload_out
    message = update.message
    raw = message.text or message.caption or ''
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else '')
    unique = message.photo[-1].file_unique_id if message.photo else (message.document.file_unique_id if message.document else '')
    key = f'{offer_id}:{unique or str(uid) + ":" + str(message.message_id)}'
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
    if not gate:
        raise ValueError('deal_missing')
    payload['description'] = f"آگهی {int(gate['advert_rowid'])}"
    currency = str(payload.get('_receipt_currency') or '').lower()
    payload['_foreign_currency'] = currency in {'eur', 'euro', 'usd'} or bool(re.search(r'€|\bEUR\b|یورو', raw, re.I))
    if payload['_foreign_currency']:
        payload['iran_amount'] = 0
    payload = _receipt_validation(payload, raw)
    payload['_receipt_kind'] = kind
    draft_id = store.create(offer_id, uid, key, payload)
    if file_id:
        await _track_source(uid, draft_id, message, file_id)
    await auto_submit(uid, draft_id)
    await preview(context.bot, uid, draft_id)
    await _refresh_deal(context, offer_id)


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
    if not _can_manage(row, q.from_user.id):
        await q.answer('این فیش متعلق به شما نیست.', show_alert=True)
        return
    display = store.receipt_display(draft_id, q.from_user.id)
    if not display.get('prompt_mid') and not display.get('receipt_mid') and getattr(q.message, 'message_id', None):
        store.receipt_display(draft_id, q.from_user.id, prompt_mid=int(q.message.message_id))
    if row['status'] != 'draft':
        await q.answer('این درخواست قبلاً پردازش شده؛ دوباره ثبت نمی‌شود.', show_alert=True)
        await preview(context.bot, q.from_user.id, draft_id)
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
        instruction = 'اطلاعات کامل را به صورت زیر بفرستید (مبلغ ریال):\nبانک: ملت\nمقصد: ملی\nنام گیرنده: ...\nمبلغ: ...\nنوع: پایا\nتاریخ: 1405/06/17\nپس از تکمیل، خودکار ثبت می‌شود.\nبرای خروج بنویسید: انصراف'
        previous_mid = context.user_data.get('deal_outgoing_edit_prompt_mid')
        if previous_mid:
            try:
                await context.bot.edit_message_text(chat_id=q.from_user.id,
                    message_id=int(previous_mid), text=instruction)
                return
            except Exception as exc:
                if _message_unchanged(exc):
                    return
                if not _message_missing(exc):
                    log.warning('Outgoing edit instruction update failed draft=%s', draft_id)
                    return
        sent = await context.bot.send_message(q.from_user.id, instruction)
        context.user_data['deal_outgoing_edit_prompt_mid'] = int(sent.message_id)
        return
    if action not in {'yes', 'no'}:
        return
    if action == 'no':
        store.transition(draft_id, 'skipped', actor=q.from_user.id, expected=row['payload'])
        await q.answer('ثبت خروجی انجام نشد.')
    else:
        if len(parts) != 4 or parts[3] != revision(row):
            await q.answer('اطلاعات تغییر کرده؛ وضعیت فعلی فیش را بررسی کنید.', show_alert=True)
            await preview(context.bot, q.from_user.id, draft_id)
            return
        if problems(json.loads(row['payload'])):
            await q.answer('ابتدا اطلاعات را کامل کنید؛ سپس خودکار ثبت می‌شود.', show_alert=True)
            await preview(context.bot, q.from_user.id, draft_id)
            return
        try:
            await q.answer('در حال ثبت خودکار…')
        except Exception:
            log.warning('Outgoing callback acknowledgement failed draft=%s', draft_id)
        await auto_submit(q.from_user.id, draft_id, expected_revision=parts[3])
    await preview(context.bot, q.from_user.id, draft_id)
    await _refresh_deal(context, row['offer_id'])


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
    if not _can_manage(row, uid) or row['status'] != 'draft':
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
    payload = _receipt_validation(payload, text)
    payload['description'] = original['description']
    if not store.transition(draft_id, 'draft', actor=uid, payload=payload, expected=row['payload']):
        context.user_data.pop(EDIT_KEY, None)
        return True
    context.user_data.pop(EDIT_KEY, None)
    edit_prompt = context.user_data.pop('deal_outgoing_edit_prompt_mid', None)
    if edit_prompt:
        display = store.receipt_display(draft_id, uid)
        pending = set(display.get('cleanup_mids') or [])
        pending.add(edit_prompt)
        store.receipt_display(draft_id, uid, cleanup_mids=sorted(pending))
    await auto_submit(uid, draft_id)
    await preview(context.bot, uid, draft_id)
    await _refresh_deal(context, row['offer_id'])
    return True
