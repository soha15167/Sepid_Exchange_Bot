"""Admin-only receipt attachment management; never reverses accounting."""
import html
import json
import asyncio
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from config.settings import ADMIN_IDS, DEAL_SUPPORT_ADMIN_IDS
from database.db import deal_gate_get
from database.receipt_management import KINDS, archive, edit_buyer_accounting_fields, fingerprint

LABELS = {'buyer': 'تومان خریدار', 'euro': 'یورو فروشنده',
          'admin': 'تومان فروشنده — ادمین', 'viewer': 'تومان فروشنده — بررسی‌کننده'}
_UI_KEY = 'receipt_management_ui'
_FIELDS_KEY = 'deal_receipt_accounting_fields_pending'
_LOCKS = {}


async def _cleanup(context, q):
    state = context.user_data.setdefault(_UI_KEY, {'mids': [], 'retired': []})
    mids = set(state['mids'])
    message = getattr(q, 'message', None)
    text = getattr(message, 'text', '') or ''
    # Legacy navigation messages were not tracked. Only remove the clicked
    # management UI, never the main deal message or a saved receipt album.
    if message and ('— مدیریت فیش‌ها' in text or text.startswith('حذف فیش ')):
        mids.add(message.message_id)
    for mid in mids:
        try:
            await context.bot.delete_message(q.from_user.id, mid)
        except Exception:
            try:
                await context.bot.edit_message_reply_markup(q.from_user.id, mid, reply_markup=None)
            except Exception:
                pass
    state['retired'] = (state['retired'] + list(mids))[-200:]
    state['mids'] = []


def _track(context, message):
    context.user_data.setdefault(_UI_KEY, {'mids': [], 'retired': []})['mids'].append(message.message_id)


def allowed(uid):
    return uid in set(ADMIN_IDS) - set(DEAL_SUPPORT_ADMIN_IDS)


async def callback(update, context):
    q = update.callback_query
    if not q:
        return
    async with _LOCKS.setdefault(q.from_user.id, asyncio.Lock()):
        await _callback(update, context)


async def _callback(update, context):
    q = update.callback_query
    if not q or not allowed(q.from_user.id):
        if q:
            await q.answer('فقط ادمین اصلی', show_alert=True)
        return
    parts = (q.data or '').split('|')
    if len(parts) == 3 and parts[1:] == ['fields', 'cancel']:
        context.user_data.pop(_FIELDS_KEY, None)
        await q.answer('ویرایش لغو شد')
        return
    try:
        action, oid = parts[1], int(parts[2])
        gate = deal_gate_get(oid)
        if not gate:
            raise ValueError()
    except (IndexError, ValueError):
        await q.answer('معامله پیدا نشد', show_alert=True)
        return
    aid = int(gate['advert_rowid'])
    state = context.user_data.get(_UI_KEY) or {}
    if getattr(getattr(q, 'message', None), 'message_id', None) in state.get('retired', []):
        await q.answer('این منو بسته شده است؛ از پیام اصلی معامله وارد شوید.')
        return
    if action == 'close':
        await q.answer('مدیریت فیش‌ها بسته شد')
        await _cleanup(context, q)
        return
    if action == 'menu':
        await q.answer()
        await _cleanup(context, q)
        rows = [
            [Button('➕ فیش تومان خریدار', callback_data=f'adm|pxy|{oid}|brcpt')],
            [Button('➕ فیش یورو فروشنده', callback_data=f'adm|pxy|{oid}|srcpt')],
            [Button('➕ فیش تومان فروشنده', callback_data=f'adm|stom|{oid}|go')],
        ]
        for kind, column in KINDS.items():
            for idx, item in enumerate(json.loads(gate.get(column) or '[]')):
                if item.get('attachment_removed_at'):
                    continue
                if kind == 'buyer':
                    rows.append([Button(f'✏️ مبلغ فیش خریدار — {idx + 1}', callback_data=f'adm|tomamt|{oid}|{idx}')])
                    if item.get('accounting_status') not in {'submitted', 'submitting', 'duplicate', 'rejected'}:
                        rows.append([Button(f'✏️ مشخصات حسابداری فیش خریدار — {idx + 1}', callback_data=f'receipts|fields|{oid}|{idx}')])
                rows.append([Button(f'🗑 {LABELS[kind]} — {idx + 1}', callback_data=f'receipts|view|{oid}|{kind}|{idx}')])
        rows.append([Button('⬅️ بازگشت به معامله / بستن', callback_data=f'receipts|close|{oid}')])
        _track(context, await context.bot.send_message(q.from_user.id, f'آگهی {aid} — مدیریت فیش‌ها\nافزودن از مسیر معمول همان نوع فیش انجام می‌شود. برای حذف، ابتدا فیش را بازبینی کنید.', reply_markup=Markup(rows)))
        return
    if action == 'fields':
        try:
            idx = int(parts[3])
            items = json.loads(gate.get(KINDS['buyer']) or '[]')
            item = items[idx]
            if idx < 0 or item.get('attachment_removed_at') or item.get('accounting_status') in {'submitted', 'submitting', 'duplicate', 'rejected'}:
                raise ValueError()
        except (IndexError, ValueError):
            await q.answer('این فیش قابل ویرایش نیست.', show_alert=True)
            return
        context.user_data[_FIELDS_KEY] = {'offer_id': oid, 'index': idx, 'expected': fingerprint(item), 'actor_id': q.from_user.id}
        await q.answer()
        await context.bot.send_message(q.from_user.id, f'آگهی {aid} — فیش {idx + 1}\nاطلاعات را در یک پیام با این قالب بفرستید:\nنام بانک | نوع انتقال | تاریخ\nمثال: ملت | پایا | 1405/07/06', reply_markup=Markup([[Button('انصراف', callback_data='receipts|fields|cancel')]]))
        return
    try:
        kind, idx = parts[3], int(parts[4])
        items = json.loads(gate.get(KINDS[kind]) or '[]')
        if idx < 0:
            raise ValueError()
        item = items[idx]
        if item.get('attachment_removed_at'):
            raise ValueError()
    except (IndexError, KeyError, ValueError):
        await q.answer('فیش تغییر کرده یا حذف شده است', show_alert=True)
        return
    if action == 'view':
        await q.answer()
        await _cleanup(context, q)
        if item.get('file_id') and item.get('type') in ('photo', 'document'):
            send = context.bot.send_photo if item['type'] == 'photo' else context.bot.send_document
            _track(context, await send(q.from_user.id, item['file_id'], caption=f'آگهی {aid} — {LABELS[kind]} — {idx + 1}'))
        else:
            _track(context, await context.bot.send_message(q.from_user.id, str(item.get('text') or 'فیش بدون فایل')[:3500]))
        _track(context, await context.bot.send_message(q.from_user.id,
            f'حذف فیش {idx + 1} از آگهی {aid}؟\n'
            'نسخهٔ اصلی در سابقه نگهداری می‌شود. تأیید پرداخت و ثبت حسابداری برگشت نمی‌خورد. '
            'نسخه‌های ارسال‌شده به خریدار/فروشنده خودکار پاک نمی‌شوند. '
            'همهٔ پیش‌نمایش‌های خروجی تأییدنشدهٔ این معامله برای جلوگیری از ثبت فیش اشتباه لغو می‌شوند و باید دوباره بررسی شوند.',
            reply_markup=Markup([[Button('🗑 تأیید حذف', callback_data=f'receipts|remove|{oid}|{kind}|{idx}|{fingerprint(item)}')],
                                 [Button('⬅️ انصراف / بازگشت', callback_data=f'receipts|menu|{oid}')],
                                 [Button('بستن', callback_data=f'receipts|close|{oid}')]])))
    elif action == 'remove':
        if len(parts) != 6 or not archive(oid, kind, idx, parts[5], q.from_user.id):
            await q.answer('فیش تغییر کرده؛ دوباره بازبینی کنید', show_alert=True)
            return
        await q.answer('فیش حذف و در سابقه نگهداری شد')
        await _cleanup(context, q)
        for chat_id, message_id in (item.get('reviewer_notify_mids') or {}).items():
            try:
                await context.bot.delete_message(int(chat_id), int(message_id))
            except Exception:
                await context.bot.send_message(q.from_user.id, 'پاک‌کردن یک نسخهٔ بررسی‌کننده ناموفق بود؛ دکمهٔ تأیید آن غیرفعال شده است.')
        from handlers.deal_gate import sync_deal_admin_notification, _update_viewer_toman_prompts
        if kind == 'buyer':
            from handlers.deal_gate import reconcile_received_buyer_receipts
            await reconcile_received_buyer_receipts(context, oid)
        await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
        await _update_viewer_toman_prompts(context.bot, deal_gate_get(oid))
        await context.bot.send_message(q.from_user.id, f'آگهی {aid}: فیش از آلبوم حذف شد. سوابق مالی تغییر نکرد.')


async def message(update, context):
    pending = context.user_data.get(_FIELDS_KEY)
    if not pending or not update.message or not allowed(update.effective_user.id) or update.effective_user.id != pending['actor_id']:
        return False
    text = (update.message.text or '').strip()
    if text in {'انصراف', '/cancel'}:
        context.user_data.pop(_FIELDS_KEY, None)
        await update.message.reply_text('ویرایش لغو شد.')
        return True
    values = [part.strip() for part in text.split('|')]
    if len(values) != 3 or not all(values):
        await update.message.reply_text('قالب درست نیست: نام بانک | نوع انتقال | تاریخ')
        return True
    item = edit_buyer_accounting_fields(pending['offer_id'], pending['index'], pending['expected'], *values, pending['actor_id'])
    context.user_data.pop(_FIELDS_KEY, None)
    if not item:
        await update.message.reply_text('فیش تغییر کرده یا قابل ویرایش نیست؛ دوباره از مدیریت فیش‌ها شروع کنید.')
        return True
    from handlers.deal_gate import sync_deal_admin_notification
    await sync_deal_admin_notification(context.bot, pending['offer_id'], deal_complete=True)
    await update.message.reply_text('✅ مشخصات ثبت شد. اکنون «تومان نشست» را دوباره تأیید کنید.')
    return True
