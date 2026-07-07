"""Thin shim to the shared fleet-wide [ClaudeFin] email-alert helper.

The logic lives ONCE in `<workspace>/_shared/email_alert` (a sibling of this
repo; see CONVENTIONS.md "Email alerts ([ClaudeFin])"). This file only finds +
imports it and degrades loudly when it is missing — an email alert must NEVER
gate report generation, so `send_alert` here never raises and returns False on
any failure (the shared sender has the same contract).
"""
from __future__ import annotations

import sys
from pathlib import Path

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_PKG_PARENT = _WORKSPACE_ROOT / "_shared" / "email_alert"

_mod = None
_warned = False


def _get():
    """Import the shared package once; warn (once) and return None if absent."""
    global _mod, _warned
    if _mod is not None:
        return _mod
    try:
        if str(_PKG_PARENT) not in sys.path:
            sys.path.insert(0, str(_PKG_PARENT))
        import email_alert  # type: ignore

        _mod = email_alert
    except Exception as e:  # noqa: BLE001 — degrade loudly, never gate
        if not _warned:
            print(
                f"[WARN] shared email_alert unavailable ({e}); "
                "[ClaudeFin] email alerts disabled for this run",
                file=sys.stderr,
            )
            _warned = True
    return _mod


def send_alert(project: str, subject: str, body_text: str, **kwargs) -> bool:
    """Proxy to email_alert.send_alert. Never raises; False when unavailable/failed."""
    mod = _get()
    if mod is None:
        return False
    return mod.send_alert(project, subject, body_text, **kwargs)
