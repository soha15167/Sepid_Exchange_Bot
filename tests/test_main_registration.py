"""Verify the Telegram application is wired without opening a network connection."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from telegram.ext import CallbackQueryHandler, CommandHandler

import main as bot_main


class _FakeApplication:
    def __init__(self):
        self.handlers: list[tuple[object, int]] = []
        self.error_handlers: list[object] = []
        self.post_init = None
        self.polling_called = False

    def add_handler(self, handler, group=0):
        self.handlers.append((handler, group))

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)

    def run_polling(self):
        self.polling_called = True


def _pattern_text(handler: CallbackQueryHandler) -> str:
    pattern = handler.pattern
    return str(getattr(pattern, "pattern", pattern))


class MainRegistrationTests(unittest.TestCase):
    def test_main_registers_critical_handlers_before_polling(self):
        application = _FakeApplication()
        with (
            patch.object(bot_main, "_create_application", return_value=application),
            patch.object(bot_main, "ensure_schema") as ensure_schema,
            patch("utils.app_logging.setup_app_logging"),
        ):
            bot_main.main()

        ensure_schema.assert_called_once_with()
        self.assertTrue(application.polling_called)
        self.assertTrue(callable(application.post_init))
        self.assertEqual(application.error_handlers, [bot_main.global_error_handler])
        self.assertGreaterEqual(len(application.handlers), 65)

        groups = {group for _, group in application.handlers}
        self.assertTrue({-1, 0, 1, 2, 3, 4, 5, 6, 7, 8}.issubset(groups))

        commands: set[str] = set()
        callback_patterns: set[str] = set()
        for handler, _ in application.handlers:
            if isinstance(handler, CommandHandler):
                commands.update(handler.commands)
            if isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
                callback_patterns.add(_pattern_text(handler))

        self.assertTrue(
            {"start", "menu", "admin", "cards", "txin", "txout"}.issubset(commands)
        )
        self.assertTrue(
            {
                "^main_services$",
                r"^(deal\||adm\|dg\|)",
                r"^adm\|",
                r"^offer_\d+$",
                "^confirm_advert$",
            }.issubset(callback_patterns),
            f"missing critical callback patterns: {sorted(callback_patterns)}",
        )


if __name__ == "__main__":
    unittest.main()
