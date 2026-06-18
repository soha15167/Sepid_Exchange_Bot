from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from telegram import Bot

from config.settings import BOT_TOKEN, LIST_RECENT_LIMIT
from database.db import (
    count_euro_adverts_owned_by_user,
    delete_euro_advert_for_owner,
    get_db,
    get_euro_advert_by_rowid,
    list_euro_adverts_owned_by_user,
    update_euro_advert_field_for_owner,
    user_advert_has_active_offers,
)
from database.web_auth import list_public_euro_adverts
from services.advert_create_flow import (
    get_create_flow_config,
    validate_euro_advert,
    validate_exchange_advert,
)
from services.advert_publish import (
    delete_advert_channel_message,
    publish_euro_advert_to_channel,
    refresh_advert_on_channel,
)
from services.advert_serialize import serialize_advert_for_web
from services.channel_membership_web import check_user_can_publish_advert
from web_api.deps import get_current_user, get_optional_user
from web_api.schemas import (
    AdvertCreateRequest,
    AdvertExchangeCreateRequest,
    AdvertUpdateRequest,
)

router = APIRouter(prefix="/adverts", tags=["adverts"])


def _bot() -> Bot | None:
    return Bot(token=BOT_TOKEN) if BOT_TOKEN else None


async def _require_channel_member(uid: int) -> None:
    status = await check_user_can_publish_advert(_bot(), uid)
    if not status.get("allowed"):
        raise HTTPException(status_code=403, detail=status.get("message") or "عضویت کانال الزامی است.")


def _advert_dict(adv: dict, *, viewer_id: int | None = None) -> dict:
    return serialize_advert_for_web(adv, viewer_id=viewer_id)


@router.get("")
def list_adverts(
    page: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    user: dict | None = Depends(get_optional_user),
):
    viewer_id = int(user["telegram_id"]) if user else None
    off = page * limit
    rows, total = list_public_euro_adverts(limit=limit, offset=off)
    pages = max(1, (total + limit - 1) // limit) if total else 1
    return {
        "items": [_advert_dict(r, viewer_id=viewer_id) for r in rows],
        "page": page,
        "pages": pages,
        "total": total,
    }


@router.get("/mine")
def my_adverts(
    page: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    uid = int(user["telegram_id"])
    lim = LIST_RECENT_LIMIT
    off = page * lim
    total = count_euro_adverts_owned_by_user(uid)
    rows = list_euro_adverts_owned_by_user(uid, limit=lim, offset=off)
    pages = max(1, (total + lim - 1) // lim) if total else 1
    enriched: list[dict] = []
    for r in rows:
        full = get_euro_advert_by_rowid(int(r["rowid"])) or r
        enriched.append(_advert_dict(full, viewer_id=uid))
    return {
        "items": enriched,
        "page": page,
        "pages": pages,
        "total": total,
    }


@router.get("/meta/create-flow")
def create_flow_meta(_: dict = Depends(get_current_user)):
    return get_create_flow_config()


@router.get("/meta/channel-membership")
async def channel_membership_meta(user: dict = Depends(get_current_user)):
    return await check_user_can_publish_advert(_bot(), int(user["telegram_id"]))


@router.post("/preview")
def preview_euro_advert(body: AdvertCreateRequest, user: dict = Depends(get_current_user)):
    result, err = validate_euro_advert(
        user_id=int(user["telegram_id"]),
        operation=body.operation,
        euro_amount=int(body.euro_amount),
        rate_toman=int(body.rate_toman),
        description=body.description,
        methods=body.methods,
        account_country=body.account_country,
        instant_transfer=body.instant_transfer,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return result


@router.post("/exchange/preview")
def preview_exchange_advert(
    body: AdvertExchangeCreateRequest,
    user: dict = Depends(get_current_user),
):
    result, err = validate_exchange_advert(
        user_id=int(user["telegram_id"]),
        side=body.side,
        delivery=body.delivery,
        euro_amount=int(body.euro_amount),
        account_country=body.account_country,
        city_ir=body.city_ir,
        city_int=body.city_int,
        description=body.description,
        instant_transfer=body.instant_transfer,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return result


@router.get("/{advert_id}")
def get_advert(advert_id: int, user: dict | None = Depends(get_optional_user)):
    adv = get_euro_advert_by_rowid(advert_id)
    if not adv:
        raise HTTPException(status_code=404, detail="آگهی یافت نشد.")
    viewer_id = int(user["telegram_id"]) if user else None
    return _advert_dict(adv, viewer_id=viewer_id)


@router.post("")
async def create_advert(body: AdvertCreateRequest, user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    await _require_channel_member(uid)
    result, err = validate_euro_advert(
        user_id=uid,
        operation=body.operation,
        euro_amount=int(body.euro_amount),
        rate_toman=int(body.rate_toman),
        description=body.description,
        methods=body.methods,
        account_country=body.account_country,
        instant_transfer=body.instant_transfer,
    )
    if err or not result:
        raise HTTPException(status_code=400, detail=err or "داده نامعتبر.")

    draft = result["draft"]
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO euro_adverts (
                user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                account_country, instant_transfer, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'فعال')
            """,
            (
                draft["user_id"],
                draft["full_name"],
                draft["euro_amount"],
                draft["rate_toman"],
                draft["description"],
                draft["methods"],
                draft["operation"],
                draft["account_country"],
                draft["instant_transfer"],
            ),
        )
        advert_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    pub = await publish_euro_advert_to_channel(advert_id)
    if not pub.get("ok"):
        with get_db() as conn:
            conn.execute("DELETE FROM euro_adverts WHERE rowid = ?", (advert_id,))
        raise HTTPException(status_code=500, detail=pub.get("error") or "انتشار ناموفق")

    adv = get_euro_advert_by_rowid(advert_id)
    return {"ok": True, "advert": _advert_dict(adv or {}, viewer_id=uid), "publish": pub}


@router.post("/exchange")
async def create_exchange_advert(
    body: AdvertExchangeCreateRequest,
    user: dict = Depends(get_current_user),
):
    uid = int(user["telegram_id"])
    await _require_channel_member(uid)
    result, err = validate_exchange_advert(
        user_id=uid,
        side=body.side,
        delivery=body.delivery,
        euro_amount=int(body.euro_amount),
        account_country=body.account_country,
        city_ir=body.city_ir,
        city_int=body.city_int,
        description=body.description,
        instant_transfer=body.instant_transfer,
    )
    if err or not result:
        raise HTTPException(status_code=400, detail=err or "داده نامعتبر.")

    draft = result["draft"]
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO euro_adverts (
                user_id, full_name, euro_amount, rate_toman, description, methods, operation,
                city_ir, city_int, account_country, instant_transfer, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'فعال')
            """,
            (
                draft["user_id"],
                draft["full_name"],
                draft["euro_amount"],
                draft["rate_toman"],
                draft["description"],
                draft["methods"],
                draft["operation"],
                draft["city_ir"],
                draft["city_int"],
                draft["account_country"],
                draft["instant_transfer"],
            ),
        )
        advert_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    pub = await publish_euro_advert_to_channel(advert_id)
    if not pub.get("ok"):
        with get_db() as conn:
            conn.execute("DELETE FROM euro_adverts WHERE rowid = ?", (advert_id,))
        raise HTTPException(status_code=500, detail=pub.get("error") or "انتشار ناموفق")

    adv = get_euro_advert_by_rowid(advert_id)
    return {"ok": True, "advert": _advert_dict(adv or {}, viewer_id=uid), "publish": pub}


@router.patch("/{advert_id}")
async def update_advert(
    advert_id: int,
    body: AdvertUpdateRequest,
    user: dict = Depends(get_current_user),
):
    uid = int(user["telegram_id"])
    if user_advert_has_active_offers(advert_id):
        raise HTTPException(status_code=409, detail="پیشنهاد فعال — ویرایش مجاز نیست.")

    field_map = {
        "euro_amount": body.euro_amount,
        "rate_toman": body.rate_toman,
        "description": body.description.strip() if body.description else None,
        "account_country": body.account_country.strip() if body.account_country else None,
        "instant_transfer": body.instant_transfer,
        "city_ir": body.city_ir.strip() if body.city_ir else None,
        "city_int": body.city_int.strip() if body.city_int else None,
    }
    if body.methods is not None:
        from services.payment_methods import validate_payment_methods

        cleaned, m_err = validate_payment_methods(body.methods)
        if m_err:
            raise HTTPException(status_code=400, detail=m_err)
        field_map["methods"] = ", ".join(cleaned)

    for field, value in field_map.items():
        if value is None:
            continue
        ok = update_euro_advert_field_for_owner(advert_id, uid, field, str(value))
        if not ok:
            raise HTTPException(status_code=404, detail="آگهی یافت نشد یا ویرایش مجاز نیست.")

    await refresh_advert_on_channel(advert_id)
    adv = get_euro_advert_by_rowid(advert_id)
    return {"ok": True, "advert": _advert_dict(adv or {}, viewer_id=uid)}


@router.delete("/{advert_id}")
async def remove_advert(advert_id: int, user: dict = Depends(get_current_user)):
    uid = int(user["telegram_id"])
    ok, ch_mid, ch_cid = delete_euro_advert_for_owner(advert_id, uid)
    if not ok:
        raise HTTPException(status_code=409, detail="حذف ممکن نیست (پیشنهاد فعال یا آگهی نامعتبر).")
    await delete_advert_channel_message(ch_cid, ch_mid)
    return {"ok": True}
