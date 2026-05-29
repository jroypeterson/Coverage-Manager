"""Tests for reporting.excel worksheet-title sanitization.

Regression: the segment renamed to "Following: Non-HC" (2026-05-03) contains a
colon, which openpyxl rejects in a sheet title, so the Excel step failed with
"Invalid character : found in sheet title" while the HTML path was unaffected.
"""

import openpyxl
import pandas as pd
import pytest

from reporting.excel import excel_safe_title, write_excel_sheet


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Following: Non-HC", "Following- Non-HC"),
        ("Biopharma", "Biopharma"),
        ("HC Svcs & MedTech", "HC Svcs & MedTech"),
        ("a/b\\c*d?e:f[g]h", "a-b-c-d-e-f-g-h"),
        ("", "Sheet"),  # empty -> fallback
        (":::", "---"),  # invalid chars each map to '-' (valid title)
    ],
)
def test_excel_safe_title_sanitizes(raw, expected):
    assert excel_safe_title(raw) == expected


def test_excel_safe_title_truncates_to_31_chars():
    long_name = "X" * 50
    out = excel_safe_title(long_name)
    assert len(out) == 31
    assert out == "X" * 31


def test_excel_safe_title_no_invalid_chars_remain():
    out = excel_safe_title("Following: Non-HC / Other [misc]")
    for ch in "\\/*?:[]":
        assert ch not in out


def test_write_excel_sheet_accepts_colon_segment_name():
    """The real failure mode: a colon-containing tab name must not raise."""
    wb = openpyxl.Workbook()
    df = pd.DataFrame(
        {"Ticker": ["AAA", "BBB"], "Company Name": ["A Co", "B Co"]}
    )
    # Should not raise "Invalid character : found in sheet title".
    write_excel_sheet(wb, "Following: Non-HC", df, info_cols=["Ticker", "Company Name"])
    assert "Following- Non-HC" in wb.sheetnames
