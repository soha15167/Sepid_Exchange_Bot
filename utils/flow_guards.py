"""Guard helpers so side routers (deal gate, Iran panel) do not hijack advert/offer wizards."""

from __future__ import annotations

from telegram.ext import ContextTypes

from models.enums import UserState

_WIZARD_TEXT_STATES = frozenset(
    {
        UserState.EURO_AMOUNT.name,
        UserState.EURO_RATE.name,
        UserState.EURO_ACCOUNT_COUNTRY.name,
        UserState.EURO_DESCRIPTION.name,
        UserState.EXCHANGE_AMOUNT.name,
        UserState.EXCHANGE_COUNTRY_INT.name,
        UserState.EXCHANGE_CITY_INT.name,
        UserState.EXCHANGE_CITY_IR.name,
        UserState.EXCHANGE_DESCRIPTION.name,
        UserState.OFFER_COUNTER_EURO.name,
        UserState.OFFER_RATE.name,
        UserState.OFFER_ACCOUNT_COUNTRY.name,
        UserState.OFFER_DESCRIPTION.name,
        UserState.OFFER_EDIT_RATE.name,
    }
)

_WIZARD_TEXT_OFFER_STEPS = frozenset(
    {"counter_euro", "rate", "account_country", "description"}
)

_OFFER_TEXT_STATES = frozenset(
    s
    for s in _WIZARD_TEXT_STATES
    if s.startswith("OFFER_")
)


def user_offer_wizard_text_step(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Typed-input steps in offer flow only (not euro advert posting)."""
    ud = context.user_data
    if not ud:
        return False
    state = (ud.get("state") or "").strip()
    if state in _OFFER_TEXT_STATES:
        return True
    step = (ud.get("offer_flow_step") or "").strip()
    return step in _WIZARD_TEXT_OFFER_STEPS


def user_advert_offer_wizard_text_step(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    True when user is on a step that expects typed input (offer / advert / exchange).
    Deal-gate account collection must yield to these steps even if a gate is open.
    """
    ud = context.user_data
    if not ud:
        return False
    state = (ud.get("state") or "").strip()
    if state in _WIZARD_TEXT_STATES:
        return True
    step = (ud.get("offer_flow_step") or "").strip()
    if step in _WIZARD_TEXT_OFFER_STEPS:
        return True
    return False


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


def user_flow_text_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Any private text flow that must not be hijacked by Iran panel / deal gate."""
    return user_ad_flow_active(context)
