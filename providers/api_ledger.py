"""Locator/shim for the shared cross-project API rate-limit ledger.

Mirror of transcripts/src/transcripts/api_ledger.py — the ledger LOGIC lives once
in `../_shared/api_rate_ledger` (sibling of this repo). This file only finds +
imports it and decides WHEN to use it (off under pytest / when disabled / when the
package can't be imported). Coverage Manager shares the AlphaVantage free key with
the `transcripts` project; reserving against this ledger before each AV OVERVIEW
call keeps the two from silently starving each other's 25-call rolling-24h budget.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# providers/api_ledger.py -> providers -> Coverage Manager -> Claude Folder (workspace)
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_PKG_PARENT = _WORKSPACE_ROOT / "_shared" / "api_rate_ledger"

_resolved = False
_mod = None


def _disabled() -> bool:
    return bool(os.environ.get("API_RATE_LEDGER_DISABLED")
                or os.environ.get("PYTEST_CURRENT_TEST"))


def get_ledger():
    global _resolved, _mod
    if _disabled():
        return None
    if _resolved:
        return _mod
    _resolved = True
    try:
        if str(_PKG_PARENT) not in sys.path:
            sys.path.insert(0, str(_PKG_PARENT))
        import api_rate_ledger  # type: ignore
        _mod = api_rate_ledger
    except Exception as e:  # pragma: no cover - defensive
        print(f"[WARN] shared api_rate_ledger unavailable ({e}); "
              f"AlphaVantage fallback runs without cross-project quota coordination.",
              file=sys.stderr)
        _mod = None
    return _mod
