"""Public offer list for web adverts — same data as channel post footer."""

from __future__ import annotations

from database.db import (
    get_euro_advert_by_rowid,
    get_user,
    list_accepted_offers_for_advert,
    list_pending_offers_for_advert,
    list_rejected_offers_for_advert,
)


def _advert_euro_amount_int(adv: dict) -> int:
    try:
        return int(adv.get("euro_amount") or 0)
    except (TypeError, ValueError):
        return 0


def offer_skips_toman_rate(advert: dict | None) -> bool:
    if not advert:
        return False
    op = (advert.get("operation") or "").strip()
    if op == "معاوضه":
        return True
    euro_ex = int(advert.get("euro_exchange") or 0) == 1
    return euro_ex and op in ("خرید", "فروش")


def proposer_public_label(row: dict) -> str:
    alias = (row.get("offer_alias_name") or "").strip()
    if alias:
        return alias
    tid = int(row.get("proposer_telegram_id") or 0)
    user = get_user(tid) if tid else None
    if user:
        dn = (user.get("display_name") or "").strip()
        if dn:
            return dn
        for key in ("full_name", "username"):
            v = (user.get(key) or "").strip()
            if v:
                return v
    return str(tid or "?")


def serialize_public_offer_row(
    row: dict,
    advert: dict,
    *,
    status: str,
    hybrid: bool,
) -> dict:
    seq = int(row.get("seq_in_advert") or row.get("id") or 0)
    rate = int(row.get("rate_toman") or 0)
    pe = int(row.get("proposed_euro_amount") or 0)
    adv_e = _advert_euro_amount_int(advert)
    eff_e = pe if pe > 0 else adv_e
    label = proposer_public_label(row)
    st = (status or "pending").strip().lower()

    skips_rate = hybrid and rate == 0
    show_euro = eff_e > 0 and pe > 0 and pe != adv_e

    return {
        "seq": seq,
        "rate_toman": None if skips_rate else rate,
        "proposed_euro_amount": eff_e if show_euro else None,
        "proposer_label": label,
        "status": st,
        "skips_toman_rate": skips_rate,
    }


def list_public_offers_for_advert(advert_rowid: int) -> dict:
    """Pending + rejected + accepted offers, sorted like channel refresh."""
    try:
        rid = int(advert_rowid)
    except (TypeError, ValueError):
        return {"items": [], "completed": False}

    advert = get_euro_advert_by_rowid(rid)
    if not advert:
        return {"items": [], "completed": False}

    hybrid = offer_skips_toman_rate(advert)
    pending = list_pending_offers_for_advert(rid)
    rejected = list_rejected_offers_for_advert(rid)
    accepted = list_accepted_offers_for_advert(rid)

    merged: list[tuple[str, dict]] = (
        [("pending", r) for r in pending]
        + [("rejected", r) for r in rejected]
        + [("accepted", r) for r in accepted]
    )
    merged.sort(key=lambda t: int(t[1]["id"]))

    items = [
        serialize_public_offer_row(r, advert, status=st, hybrid=hybrid) for st, r in merged
    ]
    return {"items": items, "completed": bool(accepted)}
