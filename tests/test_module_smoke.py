"""Smoke-import every first-party bot module without starting Telegram polling."""

from __future__ import annotations

import importlib
import unittest
import warnings
from pathlib import Path

from telegram.warnings import PTBUserWarning


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOTS = (
    "config",
    "database",
    "handlers",
    "keyboards",
    "messages",
    "models",
    "utils",
    "banking_recognition",
)


def _first_party_module_names() -> list[str]:
    names: set[str] = set()
    for package in PACKAGE_ROOTS:
        root = PROJECT_ROOT / package
        for path in root.rglob("*.py"):
            relative = path.relative_to(PROJECT_ROOT)
            parts = list(relative.with_suffix("").parts)
            if "tests" in parts or "api" in parts or "__pycache__" in parts:
                continue
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                names.add(".".join(parts))
    return sorted(names)


class ModuleSmokeTests(unittest.TestCase):
    def test_all_bot_modules_import(self):
        failures: list[str] = []
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=PTBUserWarning)
            for module_name in _first_party_module_names():
                try:
                    importlib.import_module(module_name)
                except Exception as exc:  # report every broken module in one test run
                    failures.append(
                        f"{module_name}: {type(exc).__name__}: {exc}"
                    )
        self.assertFalse(failures, "module import failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
