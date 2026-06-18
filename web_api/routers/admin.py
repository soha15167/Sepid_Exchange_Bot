from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from telegram import Bot

from config.settings import ADVERT_CHANNEL_ID, BOT_TOKEN
from services import admin_web as aw
from services import deal_gate_admin_web as dgaw
from web_api.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _bot() -> Bot | None:
    return Bot(token=BOT_TOKEN) if BOT_TOKEN else None


class UserPatchBody(BaseModel):
    full_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    username: str | None = None
    email: str | None = None
    address: str | None = None
    phone_number: str | None = None


class RestrictBody(BaseModel):
    restricted: bool
    until_ts: int | None = None


class FeePatchBody(BaseModel):
    fee_override_eur: float | None = None


class AdvertFieldBody(BaseModel):
    field: str
    value: str


class OfferRateBody(BaseModel):
    rate_toman: int = Field(..., gt=0)


class OfferEuroBody(BaseModel):
    proposed_euro_amount: int | None = Field(default=None, gt=0)


class BotToggleBody(BaseModel):
    enabled: bool
    notify_telegram: bool = True


class AdminCreateUserBody(BaseModel):
    telegram_id: int = Field(..., gt=0)
    full_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    display_name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=3, max_length=120)
    address: str = Field(..., min_length=2, max_length=300)
    phone_number: str = Field(..., min_length=8, max_length=24)
    otp_code: str | None = None


class AdminProxyOfferBody(BaseModel):
    advert_id: int = Field(..., gt=0)
    alias: str = Field(..., min_length=2, max_length=120)
    rate_toman: int = Field(..., gt=0)
    description: str = Field(..., min_length=2, max_length=3500)


class DealPartyBody(BaseModel):
    party: str = Field(..., pattern="^(buyer|seller)$")


class DealAccountBody(DealPartyBody):
    text: str = Field(..., min_length=3, max_length=2000)


class DealReceiptTextBody(DealPartyBody):
    text: str = Field(..., min_length=2, max_length=2000)


class DealTomanCardBody(BaseModel):
    card_id: str = Field(..., min_length=1, max_length=64)


class DealEuroSettledBody(BaseModel):
    receipt_index: int = Field(..., ge=0)


class DealStomTextBody(BaseModel):
    text: str = Field(..., min_length=2, max_length=3500)


def _require_bot() -> Bot:
    bot = _bot()
    if not bot:
        raise HTTPException(status_code=503, detail="ربات در دسترس نیست.")
    return bot


async def _deal_panel_response(offer_id: int) -> dict:
    panel = dgaw.build_deal_admin_panel(offer_id)
    if not panel:
        raise HTTPException(status_code=404, detail="معامله یافت نشد.")
    return {"panel": panel}


@router.get("/menu")
def admin_menu(_: dict = Depends(require_admin)):
    return {"items": aw.admin_menu()}


@router.get("/stats")
def admin_stats(_: dict = Depends(require_admin)):
    return aw.admin_stats_payload()


@router.get("/users")
def admin_users(page: int = Query(0, ge=0), admin: dict = Depends(require_admin)):
    data = aw.list_users(page=page)
    aw.audit(int(admin["telegram_id"]), "web_admin_users_list", f"page={page}")
    return data


@router.get("/users/search")
def admin_users_search(q: str = Query(..., min_length=1), admin: dict = Depends(require_admin)):
    items = aw.search_users_query(q)
    aw.audit(int(admin["telegram_id"]), "web_admin_users_search", q[:80])
    return {"items": items}


@router.get("/users/{telegram_id}")
def admin_user_detail(telegram_id: int, admin: dict = Depends(require_admin)):
    u = aw.get_user_detail(telegram_id)
    if not u:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")
    return {"user": u}


@router.patch("/users/{telegram_id}")
def admin_user_patch(telegram_id: int, body: UserPatchBody, admin: dict = Depends(require_admin)):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="فیلدی ارسال نشده.")
    ok = aw.update_user_fields(telegram_id, fields)
    aw.audit(int(admin["telegram_id"]), "web_admin_user_edit", f"id={telegram_id}")
    return {"ok": ok}


@router.delete("/users/{telegram_id}")
def admin_user_delete(telegram_id: int, admin: dict = Depends(require_admin)):
    ok = aw.remove_user(telegram_id)
    aw.audit(int(admin["telegram_id"]), "web_admin_user_delete", f"id={telegram_id}")
    return {"ok": ok}


@router.post("/users/{telegram_id}/restrict")
def admin_user_restrict(telegram_id: int, body: RestrictBody, admin: dict = Depends(require_admin)):
    ok = aw.set_restrict(telegram_id, body.restricted, until_ts=body.until_ts)
    aw.audit(
        int(admin["telegram_id"]),
        "web_admin_user_restrict",
        f"id={telegram_id} restricted={body.restricted}",
    )
    return {"ok": ok}


@router.get("/adverts")
def admin_adverts(page: int = Query(0, ge=0), admin: dict = Depends(require_admin)):
    data = aw.list_adverts(page=page)
    aw.audit(int(admin["telegram_id"]), "web_admin_adverts_list", f"page={page}")
    return data


@router.get("/adverts/{advert_id}")
def admin_advert_detail(advert_id: int, admin: dict = Depends(require_admin)):
    adv = aw.get_advert_detail(advert_id)
    if not adv:
        raise HTTPException(status_code=404, detail="آگهی یافت نشد.")
    return {"advert": adv}


@router.delete("/adverts/{advert_id}")
async def admin_advert_delete(advert_id: int, admin: dict = Depends(require_admin)):
    deleted, ch_mid = aw.delete_advert_admin(advert_id)
    if deleted and ch_mid and ADVERT_CHANNEL_ID:
        bot = _bot()
        if bot:
            try:
                await bot.delete_message(chat_id=ADVERT_CHANNEL_ID, message_id=ch_mid)
            except Exception:
                pass
    aw.audit(int(admin["telegram_id"]), "web_admin_advert_delete", f"id={advert_id}")
    return {"ok": deleted}


@router.patch("/adverts/{advert_id}/fee")
def admin_advert_fee(advert_id: int, body: FeePatchBody, admin: dict = Depends(require_admin)):
    ok = aw.update_advert_fee(advert_id, body.fee_override_eur)
    aw.audit(int(admin["telegram_id"]), "web_admin_advert_fee", f"id={advert_id}")
    return {"ok": ok}


@router.patch("/adverts/{advert_id}/field")
def admin_advert_field(advert_id: int, body: AdvertFieldBody, admin: dict = Depends(require_admin)):
    ok = aw.update_advert_field(advert_id, body.field, body.value)
    if not ok:
        raise HTTPException(status_code=400, detail="فیلد نامعتبر یا آگهی یافت نشد.")
    aw.audit(int(admin["telegram_id"]), "web_admin_advert_field", f"id={advert_id} {body.field}")
    return {"ok": ok}


@router.get("/adverts/{advert_id}/offers")
def admin_advert_offers(advert_id: int, admin: dict = Depends(require_admin)):
    return {"items": aw.list_offers_for_advert(advert_id)}


@router.get("/adverts/{advert_id}/negotiations")
def admin_advert_negotiations(advert_id: int, admin: dict = Depends(require_admin)):
    return aw.negotiations_report(advert_id)


@router.delete("/offers/{offer_id}")
def admin_offer_delete(offer_id: int, admin: dict = Depends(require_admin)):
    meta = aw.delete_offer(offer_id)
    if not meta:
        raise HTTPException(status_code=404, detail="پیشنهاد یافت نشد.")
    aw.audit(int(admin["telegram_id"]), "web_admin_offer_delete", f"id={offer_id}")
    return {"ok": True, "meta": meta}


@router.patch("/offers/{offer_id}/rate")
def admin_offer_rate(offer_id: int, body: OfferRateBody, admin: dict = Depends(require_admin)):
    ok = aw.update_offer_rate(offer_id, body.rate_toman)
    aw.audit(int(admin["telegram_id"]), "web_admin_offer_rate", f"id={offer_id}")
    return {"ok": ok}


@router.patch("/offers/{offer_id}/proposed-euro")
def admin_offer_euro(offer_id: int, body: OfferEuroBody, admin: dict = Depends(require_admin)):
    ok = aw.update_offer_proposed_euro(offer_id, body.proposed_euro_amount)
    aw.audit(int(admin["telegram_id"]), "web_admin_offer_euro", f"id={offer_id}")
    return {"ok": ok}


@router.get("/deal-gates")
def admin_deal_gates(limit: int = Query(25, ge=1, le=50), admin: dict = Depends(require_admin)):
    return {"items": aw.list_deal_gates(limit=limit)}


@router.get("/deal-gates/lookup")
async def admin_deal_gate_lookup(
    offer_id: int | None = None,
    advert_id: int | None = None,
    admin: dict = Depends(require_admin),
):
    row = aw.lookup_deal_gate(offer_id=offer_id, advert_id=advert_id)
    if not row:
        raise HTTPException(status_code=404, detail="معامله یافت نشد.")
    oid = int(row["offer_id"])
    panel = dgaw.build_deal_admin_panel(oid)
    return {"gate": row, "panel": panel}


@router.get("/deal-gates/{offer_id}/panel")
async def admin_deal_gate_panel(offer_id: int, admin: dict = Depends(require_admin)):
    return await _deal_panel_response(offer_id)


@router.get("/deal-gates/{offer_id}/outbound-log")
def admin_deal_outbound_log(offer_id: int, admin: dict = Depends(require_admin)):
    return {"items": dgaw.list_outbound_log(offer_id)}


@router.post("/deal-gates/{offer_id}/resync")
async def admin_deal_resync(offer_id: int, admin: dict = Depends(require_admin)):
    bot = _require_bot()
    ok, err = await dgaw.run_resync(bot, offer_id=offer_id)
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_resync", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "همگام‌سازی ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/replay-outbound")
async def admin_deal_replay_outbound(offer_id: int, admin: dict = Depends(require_admin)):
    bot = _require_bot()
    aid = int(admin["telegram_id"])
    ok, err = await dgaw.run_replay_outbound(bot, admin_id=aid, offer_id=offer_id)
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_outlog", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "بازپخش ناموفق.")
    return {"ok": True, "message": "پیام‌ها به تلگرام ادمین ارسال شد."}


@router.post("/deal-gates/{offer_id}/proxy-yes")
async def admin_deal_proxy_yes(
    offer_id: int, body: DealPartyBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, msg = await dgaw.run_proxy_yes(
        bot, admin_id=int(admin["telegram_id"]), offer_id=offer_id, party=body.party
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_pxy_yes", f"offer={offer_id} {body.party}")
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "عملیات ناموفق.")
    result = await _deal_panel_response(offer_id)
    result["message"] = msg
    return result


@router.post("/deal-gates/{offer_id}/send-toman-card")
async def admin_deal_send_toman_card(
    offer_id: int, body: DealTomanCardBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, msg = await dgaw.run_send_toman_card(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        card_id=body.card_id,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_pay", f"offer={offer_id} card={body.card_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "ارسال کارت ناموفق.")
    result = await _deal_panel_response(offer_id)
    result["message"] = msg
    return result


@router.post("/deal-gates/{offer_id}/toman-settled")
async def admin_deal_toman_settled(offer_id: int, admin: dict = Depends(require_admin)):
    bot = _require_bot()
    ok, msg = await dgaw.run_toman_settled(
        bot, admin_id=int(admin["telegram_id"]), offer_id=offer_id
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_tomset", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "عملیات ناموفق.")
    result = await _deal_panel_response(offer_id)
    result["message"] = msg
    return result


@router.post("/deal-gates/{offer_id}/send-buyer-eur-account")
async def admin_deal_send_buyer_eur(offer_id: int, admin: dict = Depends(require_admin)):
    bot = _require_bot()
    ok, msg = await dgaw.run_send_buyer_eur_account(
        bot, admin_id=int(admin["telegram_id"]), offer_id=offer_id
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_buyeur", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "عملیات ناموفق.")
    result = await _deal_panel_response(offer_id)
    result["message"] = msg
    return result


@router.post("/deal-gates/{offer_id}/euro-settled")
async def admin_deal_euro_settled(
    offer_id: int, body: DealEuroSettledBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, msg = await dgaw.run_euro_settled(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        receipt_index=body.receipt_index,
    )
    aw.audit(
        int(admin["telegram_id"]),
        "web_admin_deal_eurcfm",
        f"offer={offer_id} idx={body.receipt_index}",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg or "عملیات ناموفق.")
    result = await _deal_panel_response(offer_id)
    result["message"] = msg
    return result


@router.post("/deal-gates/{offer_id}/account")
async def admin_deal_account(
    offer_id: int, body: DealAccountBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, err = await dgaw.run_save_account(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        party=body.party,
        text=body.text,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_account", f"offer={offer_id} {body.party}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت حساب ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/account/photo")
async def admin_deal_account_photo(
    offer_id: int,
    admin: dict = Depends(require_admin),
    party: str = Form(...),
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    if party not in ("buyer", "seller"):
        raise HTTPException(status_code=400, detail="نقش نامعتبر.")
    bot = _require_bot()
    raw = await file.read()
    ok, err = await dgaw.run_save_account_photo(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        party=party,
        file_bytes=raw,
        filename=file.filename or "account.jpg",
        caption=caption,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_account_photo", f"offer={offer_id} {party}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت عکس ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/proxy-receipt")
async def admin_deal_proxy_receipt(
    offer_id: int, body: DealReceiptTextBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, err = await dgaw.run_proxy_receipt_text(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        party=body.party,
        text=body.text,
    )
    aw.audit(
        int(admin["telegram_id"]),
        "web_admin_deal_proxy_rcpt",
        f"offer={offer_id} {body.party}",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت فیش ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/proxy-receipt/photo")
async def admin_deal_proxy_receipt_photo(
    offer_id: int,
    admin: dict = Depends(require_admin),
    party: str = Form(...),
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    if party not in ("buyer", "seller"):
        raise HTTPException(status_code=400, detail="نقش نامعتبر.")
    bot = _require_bot()
    raw = await file.read()
    ok, err = await dgaw.run_proxy_receipt_photo(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        party=party,
        file_bytes=raw,
        filename=file.filename or "receipt.jpg",
        caption=caption,
    )
    aw.audit(
        int(admin["telegram_id"]),
        "web_admin_deal_proxy_rcpt_photo",
        f"offer={offer_id} {party}",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت فیش ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/seller-toman-receipt")
async def admin_deal_seller_toman_receipt(
    offer_id: int, body: DealStomTextBody, admin: dict = Depends(require_admin)
):
    bot = _require_bot()
    ok, err = await dgaw.run_seller_toman_receipt_text(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        text=body.text,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_stom", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت فیش ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/deal-gates/{offer_id}/seller-toman-receipt/photo")
async def admin_deal_seller_toman_receipt_photo(
    offer_id: int,
    admin: dict = Depends(require_admin),
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    bot = _require_bot()
    raw = await file.read()
    ok, err = await dgaw.run_seller_toman_receipt_photo(
        bot,
        admin_id=int(admin["telegram_id"]),
        offer_id=offer_id,
        file_bytes=raw,
        filename=file.filename or "receipt.jpg",
        caption=caption,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_deal_stom_photo", f"offer={offer_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت فیش ناموفق.")
    return await _deal_panel_response(offer_id)


@router.post("/bot/toggle")
async def admin_bot_toggle(body: BotToggleBody, admin: dict = Depends(require_admin)):
    aid = int(admin["telegram_id"])
    if body.notify_telegram and _bot():
        result = await aw.toggle_bot_with_telegram(_bot(), enabled=body.enabled, admin_id=aid)
    else:
        result = aw.toggle_bot(enabled=body.enabled)
        aw.audit(aid, "bot_enable" if body.enabled else "bot_disable", "web")
    return result


@router.patch("/adverts/{advert_id}/status")
def admin_set_advert_status(
    advert_id: int,
    status: str,
    admin: dict = Depends(require_admin),
):
    from database.db import update_euro_advert_status

    ok = update_euro_advert_status(advert_id, status)
    aw.audit(int(admin["telegram_id"]), "web_admin_advert_status", f"id={advert_id} status={status}")
    return {"ok": ok}


@router.post("/users/create")
def admin_create_user(body: AdminCreateUserBody, admin: dict = Depends(require_admin)):
    result = aw.create_user_admin(
        telegram_id=body.telegram_id,
        full_name=body.full_name,
        last_name=body.last_name,
        display_name=body.display_name,
        email=body.email,
        address=body.address,
        phone_number=body.phone_number,
        otp_code=body.otp_code,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_add_user", f"id={body.telegram_id}")
    if result.get("ok"):
        return result
    if result.get("needs_otp"):
        return result
    raise HTTPException(status_code=400, detail=result.get("error") or "ثبت ناموفق.")


@router.post("/proxy-offer")
async def admin_proxy_offer(body: AdminProxyOfferBody, admin: dict = Depends(require_admin)):
    ok, err = await aw.create_proxy_offer_admin(
        _bot(),
        admin_id=int(admin["telegram_id"]),
        advert_id=body.advert_id,
        alias=body.alias,
        rate_toman=body.rate_toman,
        description=body.description,
    )
    aw.audit(int(admin["telegram_id"]), "web_admin_proxy_offer", f"advert={body.advert_id}")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ثبت ناموفق.")
    return {"ok": True}


@router.post("/broadcast/bonbast")
async def admin_broadcast_bonbast(admin: dict = Depends(require_admin)):
    ok, err = await aw.broadcast_bonbast_rates(_bot())
    aw.audit(int(admin["telegram_id"]), "web_admin_bonbast", "")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "انتشار ناموفق.")
    return {"ok": True}


@router.post("/bot/restart")
async def admin_restart_bot(admin: dict = Depends(require_admin)):
    ok, err = await aw.restart_bot_service(_bot())
    aw.audit(int(admin["telegram_id"]), "web_admin_restart", "")
    if not ok:
        raise HTTPException(status_code=400, detail=err or "ری‌استارت ناموفق.")
    return {"ok": True, "message": "فرمان ری‌استارت زمان‌بندی شد."}
