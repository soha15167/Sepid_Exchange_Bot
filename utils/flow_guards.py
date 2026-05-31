"""Guard helpers so side routers (deal gate, Iran panel) do not hijack advert/offer wizards."""

from __future__ import annotations

from telegram.ext import ContextTypes

from models.enums import UserState


def user_advert_offer_wizard_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True during advert post, offer, or exchange wizard (not negotiation/deal gate)."""
    ud = context.user_data
    if not ud:
        return False
    state = (ud.get("state") or "").strip()
    if any(state.startswith(p) for p in ("EURO_", "EXCHANGE_", "OFFER_")):
        return True
    if state == UserState.SERVICE_SELECTION.name:
        return True
    if (ud.get("offer_flow_step") or "").strip():
        return True
    if ud.get("admin_post_advert_for"):
        return True
    return False


def user_ad_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True when user is mid advert post, offer, exchange, or negotiation."""
    if user_advert_offer_wizard_active(context):
        return True
    ud = context.user_data
    if not ud:
        return False
    state = (ud.get("state") or "").strip()
    if state.startswith("NEGOTIATION"):
        return True
    return False
