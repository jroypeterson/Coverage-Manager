"""Tests for the performance-report email gating (reporting.generate).

Regression: `cli.py performance` calls generate.main(skip_email=False), and the
email step previously checked only sample_mode/skip_email — never
config.EMAIL_ENABLED. So a standalone run emailed even with the master switch
off, producing surprise (and on re-run, duplicate) sends on 2026-05-29.
"""

import pytest

from reporting.generate import email_skip_reason


def test_send_when_enabled_and_not_skipped():
    assert email_skip_reason(sample_mode=False, skip_email=False, email_enabled=True) is None


def test_email_enabled_false_skips_even_on_standalone_run():
    # The exact bug: standalone path (skip_email=False) must still honor the flag.
    assert (
        email_skip_reason(sample_mode=False, skip_email=False, email_enabled=False)
        == "EMAIL_ENABLED=False"
    )


def test_sample_mode_skips_first():
    assert email_skip_reason(sample_mode=True, skip_email=False, email_enabled=True) == "sample mode"


def test_skip_email_flag_skips():
    assert email_skip_reason(sample_mode=False, skip_email=True, email_enabled=True) == "skip_email"


@pytest.mark.parametrize("email_enabled", [True, False])
def test_sample_mode_takes_precedence_over_enabled_flag(email_enabled):
    # Sample runs never email regardless of the master switch.
    assert email_skip_reason(True, False, email_enabled) == "sample mode"
