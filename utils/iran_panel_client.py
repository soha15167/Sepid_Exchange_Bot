"""
utils/iran_panel_client.py — Post transactions to Iran ledger panel
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


def get_transactions(
    *, base_url: str, timeout_s: float = 10.0
) -> tuple[bool, list[dict[str, Any]] | str]:
    """Read the Iran ledger transaction list for conservative reconciliation."""
    url = base_url.rstrip("/") + "/transactions"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            if not 200 <= code < 300:
                return False, f"http_{code}"
            payload = json.load(resp)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            return False, "invalid_transaction_response"
        return True, payload
    except Exception as exc:
        return False, str(exc)


def post_transaction(*, base_url: str, payload: dict[str, Any], timeout_s: float = 10.0) -> tuple[bool, str]:
    """
    Panel endpoint (observed from page JS): POST {base_url}/transactions with JSON body.
    Returns (ok, message).
    """
    url = base_url.rstrip("/") + "/transactions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            # Some servers return empty body; ok if 2xx.
            code = int(getattr(resp, "status", 200) or 200)
            if 200 <= code < 300:
                return True, "ok"
            return False, f"http_{code}"
    except Exception as exc:
        return False, str(exc)
