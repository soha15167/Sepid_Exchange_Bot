"""Payment method options — single source: keyboards/menus.py (Telegram bot)."""

from __future__ import annotations

from keyboards.menus import (
    EXCHANGE_OPTION,
    PAYMENT_OPTIONS,
    get_payment_selection_text,
)

ALLOWED_PAYMENT_METHODS: frozenset[str] = frozenset(PAYMENT_OPTIONS)


def payment_selection_hint(operation: str) -> str:
    return get_payment_selection_text(operation)


def validate_payment_methods(methods: list[str]) -> tuple[list[str], str | None]:
    """Returns cleaned list or error message."""
    if not methods:
        return [], "حداقل یک روش انتخاب کنید (IBAN / PayPal / Wise / Revolut)."
    cleaned: list[str] = []
    for raw in methods:
        m = (raw or "").strip()
        if not m:
            continue
        if m == EXCHANGE_OPTION:
            return [], "معاوضه از مسیر جداگانه ثبت می‌شود؛ فقط IBAN/PayPal/Wise/Revolut."
        if m not in ALLOWED_PAYMENT_METHODS:
            return [], (
                f"روش «{m}» معتبر نیست. فقط: IBAN, PayPal, Wise, Revolut — مثل ربات."
            )
        if m not in cleaned:
            cleaned.append(m)
    if not cleaned:
        return [], "حداقل یک روش انتخاب کنید."
    return cleaned, None


def payment_methods_config() -> dict:
    return {
        "payment_options": list(PAYMENT_OPTIONS),
        "exchange_option": EXCHANGE_OPTION,
        "multi_select": True,
        "layout_rows": [
            PAYMENT_OPTIONS[0:2],
            PAYMENT_OPTIONS[2:4],
        ],
    }
