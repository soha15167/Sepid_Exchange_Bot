from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from telegram import Bot

from config.settings import BOT_TOKEN
from database.db import (
    deal_gate_get,
    get_advert_offer_joined,
    get_euro_advert_by_rowid,
    list_incoming_pending_offers_for_advert_owner,
    list_my_pending_offers_all,
)
from services.advert_publish import refresh_advert_on_channel
from services.deal_gate_web import (
    enrich_deal_status,
    submit_account_text,
    submit_party_response,
    submit_receipt_photo,
    submit_receipt_text,
)
from services.negotiation_web import post_negotiation_message
from services.offer_flow import get_offer_flow_config, notify_offer_created, validate_and_submit_offer
from services.offer_owner_actions import (
    accept_offer_as_owner,
    enrich_offer_row,
    reject_offer_as_owner,
    update_offer_rate_as_proposer,
    withdraw_offer_as_proposer,
)
from web_api.deps import get_current_user
from web_api.schemas import (
    DealAccountRequest,
    DealReceiptRequest,
    DealResponseRequest,
    NegotiationPostRequest,
    OfferCreateRequest,
    OfferRateUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["offers"])

_GATE_STATUS_FA = {
    "pending": "در انتظار تأیید نهایی",
    "accounts": "ثبت حساب بانکی",
    "completed": "تأیید حساب — مرحله پرداخت",
    "closed": "بسته شده",
    "rejected": "رد شده",
}


def _bot() -> Bot | None:
    return Bot(token=BOT_TOKEN) if BOT_TOKEN else None


def _offer_dict(row: dict) -> dict:
    return enrich_offer_row(row)


def _list_all_offers_for_proposer(uid: int, limit: int = 80) -> list[dict]:
    import sqlite3

    from config.settings import DB_PATH
    from database.db import _table_columns

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        acols = _table_columns(conn, "advert_offers")
        seq_expr = "COALESCE(seq_in_advert, id)" if "seq_in_advert" in acols else "id"
        pe_sel = (
            "COALESCE(proposed_euro_amount, 0)"
            if "proposed_euro_amount" in acols
            else "0"
        )
        rows = cur.execute(
            f"""
            SELECT o.id, o.advert_rowid, o.proposer_telegram_id, o.rate_toman,
                   o.description, o.status, u.user_id AS owner_id,
                   {seq_expr} AS seq_in_advert,
                   {pe_sel} AS proposed_euro_amount,
                   o.created_at
            FROM advert_offers o
            INNER JOIN euro_adverts u ON u.rowid = o.advert_rowid
            WHERE o.proposer_telegram_id = ?
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (uid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/adverts/{advert_id}/offer-flow")
def offer_flow_config(advert_id: int, user: dict = Depends(get_current_user)):
    cfg = get_offer_flow_config(advert_id, int(user["telegram_id"]))
    if not cfg:
        raise HTTPException(status_code=404, detail="آگهی یافت نشد.")
    return cfg


@router.get("/adverts/{advert_id}/offers")
def list_advert_offers(advert_id: int, user: dict = Depends(get_current_user)):
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv:
        raise HTTPException(status_code=404, detail="آگهی یافت نشد.")
    uid = int(user["telegram_id"])
    if int(adv.get("user_id") or 0) != uid:
        raise HTTPException(status_code=403, detail="فقط صاحب آگهی.")
    from database.db import list_advert_offers_joined_for_advert

    rows = list_advert_offers_joined_for_advert(advert_id, limit=100)
    return {"items": [_offer_dict(r) for r in rows]}


@router.post("/adverts/{advert_id}/offers")
async def create_offer(
    advert_id: int,
    body: OfferCreateRequest,
    user: dict = Depends(get_current_user),
):
    uid = int(user["telegram_id"])
    result, err = validate_and_submit_offer(
        advert_id=advert_id,
        user_id=uid,
        mode=body.mode,
        rate_toman=int(body.rate_toman),
        description=body.description,
        proposed_euro_amount=body.proposed_euro_amount,
        proposer_account_country=body.proposer_account_country,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not result:
        raise HTTPException(status_code=400, detail="ثبت پیشنهاد ناموفق.")

    bot = _bot()
    if bot:
        try:
            await notify_offer_created(bot, result=result, advert_id=advert_id, user_id=uid)
        except Exception as exc:
            logger.warning("notify offer created failed: %s", exc)

    await refresh_advert_on_channel(advert_id)
    return {"ok": True, **result}


@router.get("/offers/mine")
def my_offers(user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    rows = _list_all_offers_for_proposer(uid)
    items = []
    for r in rows:
        d = _offer_dict(r)
        adv = get_euro_advert_by_rowid(int(r["advert_rowid"]))
        d["advert_operation"] = adv.get("operation") if adv else None
        d["advert_euro_amount"] = adv.get("euro_amount") if adv else None
        d["has_deal_gate"] = deal_gate_get(int(r["id"])) is not None
        items.append(d)
    return {"items": items}


@router.get("/offers/incoming")
def incoming_offers(user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    raw = list_incoming_pending_offers_for_advert_owner(uid)
    items = []
    for r in raw:
        oid = int(r["id"])
        meta = get_advert_offer_joined(oid)
        if not meta:
            continue
        d = _offer_dict(meta)
        adv = get_euro_advert_by_rowid(int(r["advert_rowid"]))
        d["advert_operation"] = adv.get("operation") if adv else None
        d["advert_euro_amount"] = adv.get("euro_amount") if adv else None
        d["skips_toman_rate"] = r.get("skips_toman_rate_offer", False)
        items.append(d)
    return {"items": items}


@router.get("/offers/pending")
def my_pending_offers(user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    raw = list_my_pending_offers_all(uid)
    items = []
    for r in raw:
        meta = get_advert_offer_joined(int(r["id"]))
        if meta:
            d = _offer_dict(meta)
            d["skips_toman_rate"] = r.get("skips_toman_rate_offer", False)
            items.append(d)
    return {"items": items}


@router.post("/offers/{offer_id}/accept")
async def accept_offer(offer_id: int, user: dict = Depends(get_current_user)):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    ok, err = await accept_offer_as_owner(bot, offer_id=offer_id, owner_id=int(user["telegram_id"]))
    if not ok:
        raise HTTPException(status_code=400, detail=err or "عملیات ناموفق.")
    meta = get_advert_offer_joined(offer_id)
    return {"ok": True, "offer": _offer_dict(meta or {}), "deal_started": True}


@router.post("/offers/{offer_id}/reject")
async def reject_offer(offer_id: int, user: dict = Depends(get_current_user)):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    ok, err = await reject_offer_as_owner(bot, offer_id=offer_id, owner_id=int(user["telegram_id"]))
    if not ok:
        raise HTTPException(status_code=400, detail=err or "عملیات ناموفق.")
    meta = get_advert_offer_joined(offer_id)
    return {"ok": True, "offer": _offer_dict(meta or {})}


@router.delete("/offers/{offer_id}")
async def withdraw_offer(offer_id: int, user: dict = Depends(get_current_user)):
    ok, err = await withdraw_offer_as_proposer(
        _bot(), offer_id=offer_id, proposer_id=int(user["telegram_id"])
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "حذف ناموفق.")
    return {"ok": True}


@router.patch("/offers/{offer_id}/rate")
async def patch_offer_rate(
    offer_id: int,
    body: OfferRateUpdateRequest,
    user: dict = Depends(get_current_user),
):
    ok, err = await update_offer_rate_as_proposer(
        _bot(),
        offer_id=offer_id,
        proposer_id=int(user["telegram_id"]),
        rate_toman=int(body.rate_toman),
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ویرایش ناموفق.")
    meta = get_advert_offer_joined(offer_id)
    return {"ok": True, "offer": _offer_dict(meta or {})}


@router.get("/deals/{offer_id}")
def deal_status(offer_id: int, user: dict = Depends(get_current_user)):
    row = get_advert_offer_joined(offer_id)
    if not row:
        raise HTTPException(status_code=404, detail="پیشنهاد یافت نشد.")
    uid = int(user["telegram_id"])
    owner = int(row.get("owner_id") or 0)
    proposer = int(row.get("proposer_telegram_id") or 0)
    if uid not in (owner, proposer):
        raise HTTPException(status_code=403, detail="دسترسی ندارید.")

    gate = deal_gate_get(offer_id)
    payload = enrich_deal_status(gate=gate, row=row, user_id=uid)
    advert = get_euro_advert_by_rowid(int(row["advert_rowid"]))
    payload["advert_operation"] = advert.get("operation") if advert else None
    return payload


_ROLE_FA = {
    "owner": "آگهی‌دهنده",
    "proposer": "پیشنهاددهنده",
    "system": "سیستم",
    "admin": "ادمین",
    "buyer": "خریدار",
    "seller": "فروشنده",
    "other": "؟",
}


@router.get("/offers/{offer_id}/negotiation")
def offer_negotiation(offer_id: int, user: dict = Depends(get_current_user)):
    from database.db import negotiation_transcript_list

    row = get_advert_offer_joined(offer_id)
    if not row:
        raise HTTPException(status_code=404, detail="پیشنهاد یافت نشد.")
    uid = int(user["telegram_id"])
    owner = int(row.get("owner_id") or 0)
    proposer = int(row.get("proposer_telegram_id") or 0)
    if uid not in (owner, proposer):
        raise HTTPException(status_code=403, detail="دسترسی ندارید.")
    st = (row.get("status") or "pending").strip().lower()
    lines = negotiation_transcript_list(offer_id)
    can_post = st == "pending"
    return {
        "offer_id": offer_id,
        "status": st,
        "can_post": can_post,
        "post_hint": None if can_post else "این پیشنهاد دیگر در وضعیت مذاکره نیست.",
        "lines": [
            {"role": _ROLE_FA.get((e.get("from") or "other"), "؟"), "text": e.get("text") or ""}
            for e in lines
        ],
    }


@router.post("/offers/{offer_id}/negotiation")
async def post_offer_negotiation(
    offer_id: int,
    body: NegotiationPostRequest,
    user: dict = Depends(get_current_user),
):
    bot = _bot()
    ok, err = await post_negotiation_message(
        bot,
        offer_id=offer_id,
        user_id=int(user["telegram_id"]),
        text=body.text,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ارسال نشد.")
    from database.db import negotiation_transcript_list

    lines = negotiation_transcript_list(offer_id)
    return {
        "ok": True,
        "lines": [
            {"role": _ROLE_FA.get((e.get("from") or "other"), "؟"), "text": e.get("text") or ""}
            for e in lines
        ],
    }


@router.post("/deals/{offer_id}/response")
async def deal_party_response(
    offer_id: int,
    body: DealResponseRequest,
    user: dict = Depends(get_current_user),
):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    ok, err = await submit_party_response(
        bot,
        offer_id=offer_id,
        user_id=int(user["telegram_id"]),
        response=body.response,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت نشد.")
    row = get_advert_offer_joined(offer_id)
    gate = deal_gate_get(offer_id)
    payload = enrich_deal_status(gate=gate, row=row or {}, user_id=int(user["telegram_id"]))
    return {"ok": True, "deal": payload}


@router.post("/deals/{offer_id}/accounts")
async def deal_account_submit(
    offer_id: int,
    body: DealAccountRequest,
    user: dict = Depends(get_current_user),
):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    ok, err = await submit_account_text(
        bot,
        offer_id=offer_id,
        user_id=int(user["telegram_id"]),
        text=body.text,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت نشد.")
    row = get_advert_offer_joined(offer_id)
    gate = deal_gate_get(offer_id)
    payload = enrich_deal_status(gate=gate, row=row or {}, user_id=int(user["telegram_id"]))
    return {"ok": True, "deal": payload}


@router.post("/deals/{offer_id}/receipts")
async def deal_receipt_submit(
    offer_id: int,
    body: DealReceiptRequest,
    user: dict = Depends(get_current_user),
):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    ok, err = await submit_receipt_text(
        bot,
        offer_id=offer_id,
        user_id=int(user["telegram_id"]),
        text=body.text,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت نشد.")
    row = get_advert_offer_joined(offer_id)
    gate = deal_gate_get(offer_id)
    payload = enrich_deal_status(gate=gate, row=row or {}, user_id=int(user["telegram_id"]))
    return {"ok": True, "deal": payload}


@router.post("/deals/{offer_id}/receipts/photo")
async def deal_receipt_photo(
    offer_id: int,
    user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    raw = await file.read()
    ok, err = await submit_receipt_photo(
        bot,
        offer_id=offer_id,
        user_id=int(user["telegram_id"]),
        file_bytes=raw,
        filename=file.filename or "receipt.jpg",
        caption=caption,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت نشد.")
    row = get_advert_offer_joined(offer_id)
    gate = deal_gate_get(offer_id)
    payload = enrich_deal_status(gate=gate, row=row or {}, user_id=int(user["telegram_id"]))
    return {"ok": True, "deal": payload}
