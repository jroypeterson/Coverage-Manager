"""`scripts/build_hc_coverage_xlsx.py` — the AA_Core Coverage workbook.

Codex reviewed the design on 2026-08-26 and its last finding was that the builder
had no tests at all, which is how a `.info`-per-field fetch shipped in 3be1f05,
throttled Yahoo for an entire session, and was found by review rather than by CI.
Every test here pins a defect that actually occurred or a rule that would publish
something private if it silently stopped holding.
"""
import datetime
import importlib.util
import os

import openpyxl
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hc_builder",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "build_hc_coverage_xlsx.py"))
b = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(b)


# ── the regression that caused the outage ────────────────────────────────────

def test_info_is_fetched_once_per_attempt_not_once_per_field(monkeypatch):
    """THE bug. It was written as:

        d = {k: (yf.Ticker(sym).info or {}).get(k) for k in FIELDS}

    which constructs a Ticker and hits `.info` once for EVERY field. Six fields x
    four retry attempts x 239 rows is up to 5,736 requests where 239 would do. It
    throttled Yahoo hard enough that three consecutive builds were refused 90,
    136 and 143 rows respectively and every one fell through to FMP. After the
    fix the next build took all 239 from Yahoo with zero fallbacks.

    One row, one healthy payload => exactly ONE `.info` access.
    """
    hits = []

    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        @property
        def info(self):
            hits.append(self.sym)
            return {"marketCap": 1e9, "regularMarketPrice": 10.0,
                    "currency": "USD", "longName": "Acme Inc"}

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": FakeTicker}))
    out = b.fetch([{"Ticker": "ACME", "Company Name": "Acme Inc",
                    "Exchange": "NASDAQ"}])
    assert out["ACME"]["marketCap"] == 1e9
    assert len(hits) == 1, (
        "`.info` was accessed %d times for ONE ticker. It is being re-fetched per "
        "field again, which is what throttled Yahoo on 2026-08-26." % len(hits))


# ── what may and may not be published ────────────────────────────────────────

def test_ratings_are_never_in_the_public_schema():
    """`docs/hc_coverage.csv` is served by GitHub Pages to anyone with the URL and
    is what the Google Sheet reads. The CSV writer serialises whatever is in COLS,
    so adding `Rating` to COLS without excluding it publishes JP's private
    judgement. Codex flagged this Critical before it shipped."""
    assert "Rating" in b.COLS
    assert "Rating" in b.PRIVATE_ONLY
    assert "Rating" not in b.PUBLIC_COLS


def test_public_schema_is_the_private_one_minus_exactly_the_private_columns():
    """Pins the relationship rather than a column count, so a new column is
    published deliberately or not at all."""
    assert set(b.PUBLIC_COLS) == set(b.COLS) - b.PRIVATE_ONLY
    assert b.PUBLIC_COLS == [c for c in b.COLS if c not in b.PRIVATE_ONLY], \
        "public column ORDER must track COLS; the Sheet reads by position"


def test_the_published_csv_on_disk_carries_no_rating_column():
    """Belt and braces: check the artifact, not just the constant. A published
    artifact is the thing consumers read, and validating the source instead of the
    artifact is how a BOM once emptied every export."""
    path = b.PUBLIC_CSV
    if not os.path.exists(path):
        pytest.skip("public CSV not built in this checkout")
    header = open(path, encoding="utf-8").readline().strip().split(",")
    assert "Rating" not in header, "the PUBLISHED csv is exposing ratings"
    assert "Ticker" in header and "Size" in header


# ── the size bucket ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("mcap, expected", [
    (22613.4, "SMID"),   # Guardant — SMID on JP's reference sheet
    (34126.0, "LC"),     # Illumina — LC on JP's reference sheet
    (b.LC_THRESHOLD_USD_M, "LC"),        # boundary is inclusive
    (b.LC_THRESHOLD_USD_M - 0.01, "SMID"),
])
def test_size_bucket(mcap, expected):
    assert b.size_bucket(mcap) == expected


def test_size_is_blank_when_market_cap_is_unknown():
    """Never a guessed bucket. The partial-book guard tolerates up to 5% of rows
    missing a market cap, so this is reachable, and the proposal that claimed it
    "cannot happen" was wrong."""
    assert b.size_bucket(None) is None


# ── forward P/E ──────────────────────────────────────────────────────────────

def test_forward_pe_passes_through_a_real_multiple():
    assert b.forward_pe(22.1) == 22.1


@pytest.mark.parametrize("raw", [-380.9, 0, None, "", "n/a"])
def test_forward_pe_blanks_anything_that_is_not_a_positive_multiple(raw):
    """Yahoo returns a NEGATIVE forwardPE for a company expected to lose money —
    Guardant came back -380.9. Sorting a column containing it puts the biggest
    loss-maker at the top as though it were the cheapest name on the sheet."""
    assert b.forward_pe(raw) is None


# ── the ratings join ─────────────────────────────────────────────────────────

def _ratings_book(tmp_path, rows):
    path = tmp_path / "Ratings_CoreCoverage.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = b.RATING_SHEET
    for j, c in enumerate(b.RATING_COLS, start=1):
        ws.cell(1, j, c)
    for i, r in enumerate(rows, start=2):
        for j, c in enumerate(b.RATING_COLS, start=1):
            ws.cell(i, j, r.get(c))
    wb.save(path)
    return str(path)


def test_a_rating_loads_for_a_matching_row(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "RATINGS_PATH", _ratings_book(tmp_path, [
        {"Ticker": "ISRG", "Company Name": "Intuitive Surgical Inc", "Rating": "2"},
    ]))
    loaded = b.load_ratings()
    assert loaded["ISRG"]["Rating"] == "2"


def test_duplicate_tickers_in_the_ratings_file_abort_rather_than_pick_one(
        tmp_path, monkeypatch):
    """A duplicated join key fans a left-join out into extra rows, and silently
    choosing one of two ratings is choosing for JP."""
    monkeypatch.setattr(b, "RATINGS_PATH", _ratings_book(tmp_path, [
        {"Ticker": "ISRG", "Company Name": "Intuitive Surgical Inc", "Rating": "2"},
        {"Ticker": "ISRG", "Company Name": "Intuitive Surgical Inc", "Rating": "4"},
    ]))
    with pytest.raises(SystemExit) as e:
        b.load_ratings()
    assert "duplicate" in str(e.value).lower()


def test_a_missing_ratings_file_is_blank_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "RATINGS_PATH", str(tmp_path / "nope.xlsx"))
    assert b.load_ratings() == {}


def test_a_rating_does_not_attach_when_the_ratings_file_names_another_company():
    """JP: "Ticker is an identity but it can be fuzzy so you need to check and
    verify with me if its too ambiguous."

    So the rating must become UNREACHABLE, not merely warned about. `ZEN` is the
    precedent: the ticker moved from Zendesk to Zentek and the stale
    classification rode along because nothing refused to use it."""
    assert b._payload_names_match("Zentek Ltd", "ZEN TECHNOLOGIES LTD") is False
    assert b._payload_names_match("Medartis Holding AG", "Medifast, Inc.") is False
    # ...while the same company spelled two ways still joins.
    assert b._payload_names_match("bioMerieux SA", "bioMérieux S.A.") is True


def test_a_blank_name_in_the_ratings_file_still_joins():
    """If JP clears a name cell the rating should still attach — an absent name is
    not evidence of a mismatch, and the alternative silently drops his work."""
    assert b._payload_names_match("", "Anything Inc") is True


# ── returns freshness ────────────────────────────────────────────────────────

def test_a_stale_snapshot_yields_blank_returns_not_stale_ones(monkeypatch, tmp_path):
    """A YTD from a month ago sitting beside a same-day price reads as one
    consistent moment and is not one. Blank is the honest answer."""
    old = tmp_path / "perf_df_2020-01-01.pkl"
    old.write_bytes(b"not-a-real-pickle")
    os.utime(old, (0, 0))  # epoch: unambiguously stale
    monkeypatch.setattr(b, "REPO", str(tmp_path))
    monkeypatch.setattr(b, "SNAPSHOT_MAX_AGE_DAYS", 10)
    cache = tmp_path / "cache" / "perf"
    cache.mkdir(parents=True)
    stale = cache / "perf_df_2020-01-01.pkl"
    stale.write_bytes(b"not-a-real-pickle")
    os.utime(stale, (0, 0))
    returns, as_of = b.load_returns()
    assert returns == {} and as_of is None, (
        "a stale snapshot must produce blank returns, never stale ones read as live")


def test_no_snapshot_at_all_is_blank_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "REPO", str(tmp_path))
    assert b.load_returns() == ({}, None)


def test_return_columns_are_named_for_calendar_years_and_ytd():
    """The workbook footnote promises exactly these, and the Sheet reads by
    position."""
    assert b.RETURN_COLS == ["2019", "2020", "2021", "2022", "2023", "2024",
                             "2025", "YTD"]
    for c in b.RETURN_COLS:
        assert c in b.COLS


# ── the rename migration ─────────────────────────────────────────────────────

def test_archiving_sweeps_a_prior_stem_out_of_the_top_level(tmp_path):
    """`archive_existing` looked only for the CURRENT stem, so the first run after
    the rename would have left `Coverage - HC Services and MedTech.xlsx` sitting
    beside `AA_Core Coverage.xlsx`, indistinguishable from a current file."""
    out = tmp_path
    old = out / ("%s.xlsx" % b.PRIOR_STEMS[0])
    old.write_bytes(b"old workbook")
    current = out / ("%s.xlsx" % b.STEM)
    current.write_bytes(b"current workbook")

    b.archive_existing(str(out), str(current))

    assert not old.exists(), "the pre-rename workbook was left at the top level"
    archived = sorted(p.name for p in (out / "archive").iterdir())
    assert any(p.startswith(b.PRIOR_STEMS[0]) for p in archived), archived
    assert any(p.startswith(b.STEM) for p in archived), archived


def test_archiving_is_a_noop_when_there_is_nothing_to_archive(tmp_path):
    assert b.archive_existing(str(tmp_path), str(tmp_path / "absent.xlsx")) is None


def test_the_stem_and_the_published_endpoint_are_decoupled():
    """The Sheet is one =IMPORTDATA() cell pointed at this exact URL and nothing
    in this repo can rewrite that cell, so renaming the workbook must never rename
    the published CSV."""
    assert b.STEM == "AA_Core Coverage"
    assert os.path.basename(b.PUBLIC_CSV) == "hc_coverage.csv"


def test_the_ratings_workbook_is_scoped_to_core_coverage():
    assert os.path.basename(b.RATINGS_PATH) == "Ratings_CoreCoverage.xlsx"
    assert b.HUMAN_COLS == {"Rating", "Notes"}
    assert not (b.HUMAN_COLS & b.MACHINE_COLS), \
        "a column cannot be both human-owned and machine-refreshed"
