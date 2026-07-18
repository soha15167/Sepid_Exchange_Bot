"""Telegram limits InlineKeyboardButton callback_data to 64 UTF-8 bytes."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from keyboards.admin_home import admin_home_inline_keyboard
from keyboards.menus import (
    admin_panel_back_keyboard,
    admin_restrict_actions_keyboard,
    generate_inline_keyboard,
    inline_cancel_keyboard,
    inline_confirm_back,
    inline_confirm_only,
    main_menu_inline_keyboard,
    registration_cancel_inline_keyboard,
    registration_otp_keyboard,
    services_inline_keyboard,
    start_inline_keyboard,
    terms_inline_keyboard,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _callback_values(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


class CallbackDataLimitTests(unittest.TestCase):
    def test_shared_runtime_keyboards_fit_telegram_limit(self):
        keyboards = [
            admin_home_inline_keyboard(),
            admin_panel_back_keyboard(),
            admin_restrict_actions_keyboard(9_223_372_036_854_775_807),
            generate_inline_keyboard(["IBAN", "Wise"]),
            inline_cancel_keyboard(),
            inline_confirm_back(),
            inline_confirm_only(),
            main_menu_inline_keyboard,
            registration_cancel_inline_keyboard,
            registration_otp_keyboard(show_telegram=True, countdown=999),
            services_inline_keyboard,
            start_inline_keyboard,
            terms_inline_keyboard,
        ]
        for keyboard in keyboards:
            for value in _callback_values(keyboard):
                with self.subTest(callback_data=value):
                    self.assertLessEqual(len(value.encode("utf-8")), 64)

    def test_literal_callback_data_in_source_fits_limit(self):
        failures: list[str] = []
        for folder in ("handlers", "keyboards"):
            for path in (PROJECT_ROOT / folder).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    for keyword in node.keywords:
                        if keyword.arg != "callback_data":
                            continue
                        if isinstance(keyword.value, ast.Constant) and isinstance(
                            keyword.value.value, str
                        ):
                            size = len(keyword.value.value.encode("utf-8"))
                            if size > 64:
                                failures.append(
                                    f"{path.name}:{node.lineno} is {size} bytes"
                                )
        self.assertFalse(failures, "oversized literal callback_data:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
