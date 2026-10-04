import fs from "node:fs";
import path from "node:path";

const [snapshotArg, sourceArg, outputArg] = process.argv.slice(2);
const snapshot = path.resolve(snapshotArg);
const source = path.resolve(sourceArg);
const output = path.resolve(outputArg);
fs.mkdirSync(output, { recursive: true });

const read = (file) => fs.readFileSync(file, "utf8").replaceAll("\r\n", "\n");
const write = (file, value) => fs.writeFileSync(file, value, "utf8");
const takeSection = (text, start, end) => {
  const left = text.indexOf(start);
  const right = text.indexOf(end, left);
  if (left < 0 || right < 0) throw new Error(`missing section: ${start}`);
  return text.slice(left, right);
};
const replaceSection = (target, sourceText, start, end) => {
  const left = target.indexOf(start);
  const right = target.indexOf(end, left);
  if (left < 0 || right < 0) throw new Error(`missing target section: ${start}`);
  return target.slice(0, left) + takeSection(sourceText, start, end) + target.slice(right);
};
const replaceOnce = (text, oldValue, newValue, label) => {
  const first = text.indexOf(oldValue);
  if (first < 0 || text.indexOf(oldValue, first + 1) >= 0) {
    throw new Error(`${label}: expected exactly one anchor`);
  }
  return text.slice(0, first) + newValue + text.slice(first + oldValue.length);
};

let prodGate = read(path.join(snapshot, "deal_gate.py"));
const localGate = read(path.join(source, "handlers", "deal_gate.py"));
if (!prodGate.includes("    deal_gate_confirm_buyer_receipt_received,\n")) {
  prodGate = replaceOnce(
    prodGate,
    "    deal_gate_buyer_receipt_list,\n",
    "    deal_gate_buyer_receipt_list,\n    deal_gate_confirm_buyer_receipt_received,\n",
    "deal gate import",
  );
}
if (!prodGate.includes("    BOT_USERNAME,\n")) {
  prodGate = replaceOnce(
    prodGate,
    "    BANK_CARDS,\n",
    "    BANK_CARDS,\n    BOT_USERNAME,\n",
    "reviewer deal link config import",
  );
}
if (!prodGate.includes("_buyer_toman_settlement_locks:")) {
  prodGate = replaceOnce(
    prodGate,
    "_admin_sync_locks: dict[int, asyncio.Lock] = {}\n",
    "_admin_sync_locks: dict[int, asyncio.Lock] = {}\n_buyer_toman_settlement_locks: dict[int, asyncio.Lock] = {}\n",
    "settlement lock",
  );
}
for (const [start, end] of [
  ["def _buyer_toman_received_reviewer_keyboard(", "def _deal_gate_allows_party_receipts("],
  ["async def _deal_admin_proxy_receipt_try_photo(", "def _buyer_toman_deposit_message_html("],
  ["async def _deal_admin_proxy_receipt_try_message(", "async def _deal_admin_proxy_receipt_try_photo("],
  ["async def deal_admin_toman_settled_callback(", "async def deal_admin_send_buyer_eur_account_callback("],
]) {
prodGate = replaceSection(prodGate, localGate, start, end);
}
const reviewerKeyboardOld = `def _buyer_toman_received_reviewer_keyboard(
    offer_id: int, receipt_index: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "✅ دریافت شد",
            callback_data=f"adm|tomset|{int(offer_id)}|{int(receipt_index)}",
        )]]
    )
`;
const reviewerKeyboardNew = `def _buyer_toman_received_reviewer_keyboard(
    offer_id: int, receipt_index: int, *, include_received: bool = True
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if include_received:
        rows.append([InlineKeyboardButton(
            "✅ دریافت شد",
            callback_data=f"adm|tomset|{int(offer_id)}|{int(receipt_index)}",
        )])
    rows.append([InlineKeyboardButton(
        "✏️ ویرایش مبلغ",
        callback_data=f"adm|tomamt|{int(offer_id)}|{int(receipt_index)}",
    )])
    return InlineKeyboardMarkup(rows)
`;
prodGate = replaceOnce(prodGate, reviewerKeyboardOld, reviewerKeyboardNew, "reviewer edit keyboard");
const reviewerMarkupOld = `    confirmed = bool(int(receipt.get("reviewer_received_at") or 0))
    markup = (
        None
        if confirmed
        else _buyer_toman_received_reviewer_keyboard(offer_id, receipt_index)
    )
`;
const reviewerMarkupNew = `    confirmed = bool(int(receipt.get("reviewer_received_at") or 0))
    gate = deal_gate_get(int(offer_id)) or {}
    settled = int(gate.get("buyer_toman_settled_at") or 0) > 0
    markup = (
        None
        if settled
        else _buyer_toman_received_reviewer_keyboard(
            offer_id, receipt_index, include_received=not confirmed
        )
    )
`;
prodGate = replaceOnce(prodGate, reviewerMarkupOld, reviewerMarkupNew, "reviewer synchronized markup");
prodGate = prodGate.replaceAll(
  'context.bot, offer_id=oid, receipt_index=buyer_index, entry_type="text", text=text',
  'context.bot, offer_id=oid, gate=gate, receipt_index=buyer_index, entry_type="text", text=text',
);
prodGate = prodGate.replaceAll(
  "context.bot, offer_id=oid, receipt_index=buyer_index, entry_type=entry_type, text=cap, file_id=fid",
  "context.bot, offer_id=oid, gate=gate, receipt_index=buyer_index, entry_type=entry_type, text=cap, file_id=fid",
);
const oldTotal = `        submitted_total = sum(
            int(item.get("amount_rial") or 0)
            for index, item in enumerate(deal_gate_buyer_receipt_list(oid))
            if index != receipt_index
            and (item.get("accounting_status") or "") == "submitted"
        )
        remaining_before = max(0, expected_rial - submitted_total)
`;
const newTotal = `        recognized_total = sum(
            int(item.get("amount_rial") or 0)
            for index, item in enumerate(deal_gate_buyer_receipt_list(oid))
            if index != receipt_index
            and (item.get("accounting_status") or "").strip().lower()
            in {"review", "ready_for_review", "submitting", "submitted", "panel_failed"}
        )
        remaining_before = max(0, expected_rial - recognized_total)
`;
prodGate = replaceOnce(prodGate, oldTotal, newTotal, "recognized total");
prodGate = replaceOnce(prodGate, "cumulative = submitted_total + amount_rial", "cumulative = recognized_total + amount_rial", "cumulative total");
prodGate = replaceOnce(
  prodGate,
  'in ("submitted", "submitting", "processing", "ready_for_review")',
  'in ("review", "ready_for_review", "submitting", "submitted", "panel_failed")',
  "duplicate statuses",
);
const syncAnchor = `        try:
            await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
`;
const syncUpgrade = `        try:
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
`;
const autoStart = prodGate.indexOf("async def _auto_account_buyer_receipt(");
const autoEnd = prodGate.indexOf("async def _send_deal_receipt_review_preview(", autoStart);
let autoBlock = prodGate.slice(autoStart, autoEnd);
autoBlock = replaceOnce(
  autoBlock,
  "            _read_receipt_image,\n",
  "            _read_receipt_image,\n            _extract_description_from_receipt,\n",
  "receipt description extractor import",
);
autoBlock = replaceOnce(
  autoBlock,
  '        payload["depositor_name"] = _buyer_dealer_name(gate)\n',
  '        receipt_description = str(payload.get("description") or "").strip()\n'
    + '        if not receipt_description and raw:\n'
    + '            receipt_description = _extract_description_from_receipt(raw)\n'
    + '        payload["depositor_name"] = _buyer_dealer_name(gate)\n',
  "capture receipt description",
);
autoBlock = replaceOnce(
  autoBlock,
  '            "fee_adjusted_to_remaining": fee_adjusted,\n',
  '            "fee_adjusted_to_remaining": fee_adjusted,\n'
    + '            "ocr_text": raw[:3000],\n'
    + '            "receipt_description": receipt_description[:500],\n',
  "persist receipt OCR text",
);
autoBlock = replaceOnce(autoBlock, syncAnchor, syncUpgrade, "auto-account sync");
prodGate = prodGate.slice(0, autoStart) + autoBlock + prodGate.slice(autoEnd);

const editAmountOld = `        fields.update(amount_rial=amount, recognized_amount_rial=amount)
`;
const editAmountNew = `        expected_rial = _buyer_expected_rial(gate)
        fields.update(
            amount_rial=amount,
            recognized_amount_rial=amount,
            expected_rial=expected_rial,
            reviewer_received_at=0,
            reviewer_received_by=0,
        )
`;
prodGate = replaceOnce(prodGate, editAmountOld, editAmountNew, "OCR amount edit totals");
const editAuthOld = `    q = update.callback_query
    if not q or not await _require_full_deal_admin(q):
        return
    items = deal_gate_buyer_receipt_list(offer_id)
`;
const editAuthNew = `    q = update.callback_query
    if not q or not q.from_user:
        return
    uid = int(q.from_user.id)
    is_full_admin = _is_full_deal_admin(uid)
    is_reviewer_amount_edit = (
        not is_full_admin
        and field == "amt"
        and uid in set(DEAL_RECEIPT_REVIEWER_IDS or [])
    )
    if not is_full_admin and not is_reviewer_amount_edit:
        await _require_full_deal_admin(q)
        return
    gate = deal_gate_get(offer_id)
    if not gate or int(gate.get("buyer_toman_settled_at") or 0) > 0:
        await q.answer("این معامله تسویه شده و مبلغ دیگر قابل ویرایش نیست.", show_alert=True)
        return
    items = deal_gate_buyer_receipt_list(offer_id)
`;
prodGate = replaceOnce(prodGate, editAuthOld, editAuthNew, "reviewer edit callback authorization");
const pendingOld = `    context.user_data[_DEAL_RCPT_EDIT_KEY] = {
        "offer_id": int(offer_id), "receipt_index": int(receipt_index), "field": field
    }
`;
const pendingNew = `    context.user_data[_DEAL_RCPT_EDIT_KEY] = {
        "offer_id": int(offer_id),
        "receipt_index": int(receipt_index),
        "field": field,
        "actor_id": uid,
        "reviewer_amount_edit": bool(is_reviewer_amount_edit),
    }
`;
prodGate = replaceOnce(prodGate, pendingOld, pendingNew, "reviewer edit pending state");
const textAuthOld = `    if not _is_full_deal_admin(int(update.effective_user.id)):
        context.user_data.pop(_DEAL_RCPT_EDIT_KEY, None)
        return False
    oid = int(pending.get("offer_id") or 0)
`;
const textAuthNew = `    uid = int(update.effective_user.id)
    is_full_admin = _is_full_deal_admin(uid)
    is_reviewer_amount_edit = (
        not is_full_admin
        and bool(pending.get("reviewer_amount_edit"))
        and int(pending.get("actor_id") or 0) == uid
        and str(pending.get("field") or "") == "amt"
        and uid in set(DEAL_RECEIPT_REVIEWER_IDS or [])
    )
    if not is_full_admin and not is_reviewer_amount_edit:
        context.user_data.pop(_DEAL_RCPT_EDIT_KEY, None)
        return False
    oid = int(pending.get("offer_id") or 0)
`;
prodGate = replaceOnce(prodGate, textAuthOld, textAuthNew, "reviewer edit text authorization");
const gateEditOld = `    if not gate or idx < 0 or idx >= len(items) or (
        items[idx].get("accounting_status") or ""
    ) not in {"ready_for_review", "panel_failed"}:
`;
const gateEditNew = `    if (
        not gate
        or int(gate.get("buyer_toman_settled_at") or 0) > 0
        or idx < 0
        or idx >= len(items)
        or (items[idx].get("accounting_status") or "")
        not in {"ready_for_review", "panel_failed"}
    ):
`;
const gateEditCount = prodGate.split(gateEditOld).length - 1;
if (gateEditCount !== 1) throw new Error(`reviewer edit receipt validation: expected 1 anchor, found ${gateEditCount}`);
prodGate = prodGate.replaceAll(gateEditOld, gateEditNew);
const valueOld = `    value = (update.message.text or "").strip()
    if not value:
`;
const valueNew = `    value = (update.message.text or "").strip()
    if value.casefold() in {"انصراف", "لغو", "cancel"}:
        context.user_data.pop(_DEAL_RCPT_EDIT_KEY, None)
        await update.message.reply_text("ویرایش مبلغ لغو شد.")
        return True
    if not value:
`;
prodGate = replaceOnce(prodGate, valueOld, valueNew, "reviewer edit cancellation");
const editReplyOld = `    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await update.message.reply_text("✅ فیلد ویرایش شد و پیش‌نمایش جدید ساخته شد.")
`;
const editReplyNew = `    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    actor_label = "بررسی‌کننده" if is_reviewer_amount_edit else "ادمین"
    _log(oid, f"{actor_label} {uid} مبلغ فیش {idx + 1} را ویرایش کرد", from_role="admin")
    await update.message.reply_text(
        f"✅ مبلغ فیش به {_persian_money(int(updated.get('amount_rial') or 0))} تغییر کرد.\\n"
        "تأیید دریافت این فیش بازنشانی شد و همهٔ پیام‌ها به‌روزرسانی شدند.",
        parse_mode=ParseMode.HTML,
    )
`;
const editSavedOld = `    if not updated:
        await update.message.reply_text("ویرایش ذخیره نشد؛ دوباره تلاش کنید.")
        return True
    await _close_deal_receipt_previews(context.bot, old)
`;
const editSavedNew = `    if not updated:
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
`;
prodGate = replaceOnce(prodGate, editSavedOld, editSavedNew, "OCR edit running totals");
const editSyncOld = `    await _send_deal_receipt_review_preview(
        context.bot, gate=gate, receipt_index=idx, receipt=updated
    )
    await update.message.reply_text("✅ فیلد ویرایش شد و پیش‌نمایش جدید ساخته شد.")
`;
const editSyncNew = `    await _send_deal_receipt_review_preview(
        context.bot, gate=gate, receipt_index=idx, receipt=updated
    )
    await _sync_buyer_receipt_reviewer_messages(
        context.bot, offer_id=oid, receipt_index=idx, receipt=updated
    )
    await sync_deal_admin_notification(context.bot, oid, deal_complete=True)
    await update.message.reply_text("✅ فیلد ویرایش شد و پیش‌نمایش جدید ساخته شد.")
`;
prodGate = replaceOnce(prodGate, editSyncOld, editSyncNew, "OCR edit synchronization");
prodGate = replaceOnce(prodGate, editReplyOld, editReplyNew, "reviewer edit confirmation");
const genericEditButton = `            [
                InlineKeyboardButton(
                    "✏️ ویرایش فیلدها",
                    callback_data=f"adm|rcptedit|{oid}|{int(receipt_index)}",
                )
            ],
`;
const directAmountButton = `            [
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
`;
prodGate = replaceOnce(prodGate, genericEditButton, directAmountButton, "direct amount edit button");
const reviewerCallbackAnchor = `    elif parts[0] == "adm" and parts[1] in {"rcptok", "rcptno"} and len(parts) >= 4:
`;
const reviewerCallbackBranch = `    elif parts[0] == "adm" and parts[1] == "tomamt" and len(parts) >= 4:
        try:
            receipt_index = int(parts[3])
        except (TypeError, ValueError):
            await q.answer("دکمه نامعتبر است.", show_alert=True)
            return
        await _handle_admin_receipt_edit_callback(
            update,
            context,
            offer_id=callback_offer_id,
            receipt_index=receipt_index,
            field="amt",
        )
`;
prodGate = replaceOnce(
  prodGate,
  reviewerCallbackAnchor,
  reviewerCallbackBranch + reviewerCallbackAnchor,
  "reviewer amount callback route",
);
write(path.join(output, "deal_gate.py"), prodGate);

let prodDb = read(path.join(snapshot, "db.py"));
const localDb = read(path.join(source, "database", "db.py"));
if (!prodDb.includes('        "reviewer_notify_mids",\n')) {
  prodDb = replaceOnce(
    prodDb,
    '        "reviewer_received_by",\n',
    '        "reviewer_received_by",\n        "reviewer_notify_mids",\n',
    "reviewer message IDs",
  );
}
if (!prodDb.includes('        "ocr_text",\n')) {
  prodDb = replaceOnce(
    prodDb,
    '        "recognition_source",\n',
    '        "recognition_source",\n        "ocr_text",\n',
    "receipt OCR text",
  );
}
if (!prodDb.includes('        "receipt_description",\n')) {
  prodDb = replaceOnce(
    prodDb,
    '        "ocr_text",\n',
    '        "ocr_text",\n        "receipt_description",\n',
    "receipt description",
  );
}
if (!prodDb.includes("def deal_gate_confirm_buyer_receipt_received(")) {
  const helper = takeSection(localDb, "def deal_gate_confirm_buyer_receipt_received(", "def deal_gate_claim_buyer_receipt_submission(");
  prodDb = replaceOnce(prodDb, "def deal_gate_has_submitted_buyer_receipt(", helper + "def deal_gate_has_submitted_buyer_receipt(", "atomic confirmation helper");
}
write(path.join(output, "db.py"), prodDb);

let prodOffers = read(path.join(snapshot, "offers.py"));
const localOffers = read(path.join(source, "handlers", "offers.py"));
const offersFunction = prodOffers.indexOf("def _buyer_toman_receipt_admin_line_html(");
const mixedStart = prodOffers.indexOf("    if slides_mode and photo_items and text_items:\n", offersFunction);
if (mixedStart >= 0) {
  const mixedEnd = prodOffers.indexOf("    if not items:\n", mixedStart);
  prodOffers = prodOffers.slice(0, mixedStart) + prodOffers.slice(mixedEnd);
}
const statusBlock = takeSection(localOffers, "    active_review_entries = [\n", '    if int(gate.get("buyer_toman_settled_at") or 0) > 0:\n');
const settled = prodOffers.indexOf('    if int(gate.get("buyer_toman_settled_at") or 0) > 0:\n', offersFunction);
if (!prodOffers.slice(offersFunction, settled).includes("    active_review_entries = [\n")) {
  prodOffers = prodOffers.slice(0, settled) + statusBlock + prodOffers.slice(settled);
}
write(path.join(output, "offers.py"), prodOffers);

let prodAccess = read(path.join(snapshot, "access_gate.py"));
const accessOld = `    if (
        u.id in set(DEAL_RECEIPT_REVIEWER_IDS or [])
        and update.callback_query
        and normalize_telegram_callback_data(update.callback_query.data or "").startswith("adm|tomset|")
    ):
        return
`;
const accessNew = `    reviewer_ids = set(DEAL_RECEIPT_REVIEWER_IDS or [])
    reviewer_callback = (
        update.callback_query
        and normalize_telegram_callback_data(update.callback_query.data or "").startswith(
            ("adm|tomset|", "adm|tomamt|")
        )
    )
    pending_edit = context.user_data.get("deal_rcpt_admin_edit_pending") or {}
    reviewer_edit_text = (
        update.message
        and isinstance(pending_edit, dict)
        and bool(pending_edit.get("reviewer_amount_edit"))
        and int(pending_edit.get("actor_id") or 0) == int(u.id)
    )
    if u.id in reviewer_ids and (reviewer_callback or reviewer_edit_text):
        return
`;
prodAccess = replaceOnce(prodAccess, accessOld, accessNew, "reviewer access gate");
write(path.join(output, "access_gate.py"), prodAccess);

let prodMain = read(path.join(snapshot, "main.py"));
prodMain = replaceOnce(
  prodMain,
  "(dg|rcptok|rcptno|rcptchk|rcptedit|rcptfld|rcptedcancel)",
  "(dg|tomamt|rcptok|rcptno|rcptchk|rcptedit|rcptfld|rcptedcancel)",
  "reviewer callback registration",
);
const localMain = read(path.join(source, "main.py"));
const reviewerReminderJob = takeSection(
  localMain,
  "def _setup_buyer_receipt_reviewer_reminder_job(",
  "def _setup_deal_delivery_retry_job(",
);
prodMain = replaceOnce(
  prodMain,
  "def _setup_deal_delivery_retry_job(",
  reviewerReminderJob + "def _setup_deal_delivery_retry_job(",
  "reviewer reminder job",
);
prodMain = replaceOnce(
  prodMain,
  "        _setup_admin_toman_receipt_reminder_job(app)\n",
  "        _setup_admin_toman_receipt_reminder_job(app)\n"
    + "        _setup_buyer_receipt_reviewer_reminder_job(app)\n",
  "reviewer reminder startup",
);
write(path.join(output, "main.py"), prodMain);

let prodStart = read(path.join(snapshot, "start_flow.py"));
prodStart = replaceOnce(
  prodStart,
  "from telegram import Update\n",
  "import re\n\nfrom telegram import Update\n",
  "deal link regex import",
);
prodStart = replaceOnce(
  prodStart,
  "from config.settings import ADMIN_IDS\n",
  "from config.settings import ADMIN_IDS, DEAL_RECEIPT_REVIEWER_IDS\n",
  "deal link reviewer config",
);
prodStart = replaceOnce(
  prodStart,
  "from database.db import get_restriction_block_message, get_user\n",
  "from database.db import (\n"
    + "    deal_gate_buyer_receipt_list,\n"
    + "    deal_gate_get,\n"
    + "    get_advert_offer_joined,\n"
    + "    get_restriction_block_message,\n"
    + "    get_user,\n"
    + ")\n",
  "deal link database imports",
);
prodStart = replaceOnce(
  prodStart,
  "    offer_ad_id = parse_offer_start_payload(list(context.args or []))\n",
  "    offer_ad_id = parse_offer_start_payload(list(context.args or []))\n"
    + "    deal_match = re.fullmatch(\n"
    + "        r\"deal_(\\d+)\", str((context.args or [\"\"])[0] or \"\"), re.I\n"
    + "    )\n",
  "deal deep-link parse",
);
const reviewerDealStart = `    if deal_match:
        offer_id = int(deal_match.group(1))
        is_reviewer = user_id in set(DEAL_RECEIPT_REVIEWER_IDS or [])
        if user_id not in set(ADMIN_IDS or []) and not is_reviewer:
            await update.message.reply_text("دسترسی به خلاصهٔ این معامله ندارید.")
            return
        gate = deal_gate_get(offer_id)
        if not gate:
            await update.message.reply_text("این معامله پیدا نشد یا دیگر در دسترس نیست.")
            return
        row = get_advert_offer_joined(offer_id) or {}
        channel_deal_number = int(
            row.get("advert_rowid") or gate.get("advert_rowid") or offer_id
        )
        receipts = deal_gate_buyer_receipt_list(offer_id)
        pending = sum(
            1 for receipt in receipts
            if not int(receipt.get("reviewer_received_at") or 0)
            and (receipt.get("accounting_status") or "").strip().lower()
            not in {"duplicate", "rejected"}
        )
        body = (
            f"<b>خلاصهٔ معامله {channel_deal_number}</b>\\n"
            f"فیش‌های ثبت‌شده: <b>{len(receipts)}</b>\\n"
            f"فیش‌های در انتظار بررسی: <b>{pending}</b>\\n\\n"
            "برای بررسی یا تأیید، به عکس فیش‌های ارسالی همین گفتگو برگردید."
        )
        await update.message.reply_text(body, parse_mode=ParseMode.HTML)
        return

`;
prodStart = replaceOnce(
  prodStart,
  "    if offer_ad_id is not None:\n",
  reviewerDealStart + "    if offer_ad_id is not None:\n",
  "deal deep-link summary",
);
write(path.join(output, "start_flow.py"), prodStart);

for (const file of ["deal_gate.py", "db.py", "offers.py", "access_gate.py", "main.py", "start_flow.py"]) console.log(path.join(output, file));
