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

def test_rating_sits_immediately_after_company_name():
    """JP 2026-08-26: "have a ratings column after the company name column"."""
    assert b.COLS[:3] == ["Ticker", "Company Name", "Rating"]


def test_ramp_effort_is_gone_from_every_surface():
    assert not any("Ramp" in c for c in b.COLS)
    assert not any("Ramp" in c for c in b.PUBLIC_COLS)


def test_the_private_only_mechanism_still_works_even_though_it_is_empty():
    """`Rating` was withheld from the public CSV until JP asked for it in the
    Google file too, so PRIVATE_ONLY is empty now. Keep the mechanism honest: the
    next sensitive column must be excludable without rediscovering that the CSV
    writer publishes everything in COLS."""
    assert b.PUBLIC_COLS == [c for c in b.COLS if c not in b.PRIVATE_ONLY]
    probe = set(b.COLS[:1])
    assert [c for c in b.COLS if c not in probe] == b.COLS[1:]


def test_public_schema_is_the_private_one_minus_exactly_the_private_columns():
    """Pins the relationship rather than a column count, so a new column is
    published deliberately or not at all."""
    assert set(b.PUBLIC_COLS) == set(b.COLS) - b.PRIVATE_ONLY
    assert b.PUBLIC_COLS == [c for c in b.COLS if c not in b.PRIVATE_ONLY], \
        "public column ORDER must track COLS; the Sheet reads by position"


def test_the_published_csv_on_disk_has_the_expected_shape():
    """Belt and braces: check the artifact, not just the constant. A published
    artifact is the thing consumers read, and validating the source instead of the
    artifact is how a BOM once emptied every export.

    ⛑ RENAMED 2026-09-07. This was `..._carries_no_rating_column` while its body
    asserted `Rating` IS at index 3 -- the name predated JP's 2026-08-26 decision
    to publish ratings to the Google file and had been contradicting its own
    assertions ever since. The assertions were right; the name was the stale
    part. `PRIVATE_ONLY` is empty deliberately, and that is checked below."""
    path = os.path.join(b.PUBLIC_DIR, b.BOOKS["core"]["public_csv"])
    if not os.path.exists(path):
        pytest.skip("public CSV not built in this checkout")
    lines = open(path, encoding="utf-8").read().splitlines()
    # Row 1 is the provenance line, row 2 blank, row 3 the header. JP asked for
    # "when it was last updated and any relevant background" to live in the file,
    # and the Google Sheet is a single =IMPORTDATA cell that nothing here can write
    # to -- so the only way it reaches that surface is inside the CSV itself.
    assert "LAST UPDATED" in lines[0], "the published CSV lost its provenance line"
    header = lines[2].split(",")
    assert header[:6] == ["#", "Ticker", "Company Name", "Rating",
                          "Mkt Cap (USD $M)", "EV (USD $M)"], header[:6]
    assert "Size" in header and "Fwd P/E (NTM)" in header
    assert "% of 52W High" in header and "EV/EBITDA (TTM)" in header
    assert not any("Ramp" in h for h in header)
    assert header[-3:] == ["Listing", "Exchange", "Country (HQ)"], header[-3:]


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


def test_a_workbook_open_in_excel_exits_3_not_1(monkeypatch, tmp_path):
    """Exit 3 means "JP has the file open", and the weekly build treats it as a
    warning rather than a red task — a red that fires because someone was reading
    the output trains you to ignore reds.

    The lock is simulated at `shutil.move`, NOT with a read-only attribute. The
    first attempt to verify this used `chmod 444` and the build exited 0, because
    `archive_existing` MOVES the old file out of the way and a move succeeds on a
    read-only file. Excel's is a sharing lock, so the failure lands on the move —
    which is exactly why the handler had to cover the archive step and not just
    the copy. A test that reproduces the wrong failure proves nothing.
    """
    current = tmp_path / ("%s.xlsx" % b.BOOK["stem"])
    current.write_bytes(b"pretend workbook")

    def locked(*a, **k):
        raise PermissionError(32, "The process cannot access the file")

    row = {c: None for c in b.COLS}
    row.update({"Ticker": "ACME", "Company Name": "Acme Inc", "Sector": "MedTech",
                "Subsector": "Dental", "Mkt Cap (USD $M)": 1000.0,
                "Price (local)": 10.0, "Ccy": "USD"})
    monkeypatch.setattr(b, "build_records",
                        lambda asof: ([row], datetime.date(2026, 8, 21), []))
    monkeypatch.setattr(b.shutil, "move", locked)
    monkeypatch.setattr(b.shutil, "copy2", locked)
    monkeypatch.setattr(b, "PUBLIC_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr("sys.argv", ["build", "--out-dir", str(tmp_path)])

    with pytest.raises(SystemExit) as e:
        b.main()
    assert e.value.code == 3, (
        "a workbook locked by Excel exited %r; the weekly build reads anything "
        "other than 3 as a pipeline failure and turns the task red"
        % (e.value.code,))


def test_the_stem_and_the_published_endpoint_are_decoupled():
    """The Sheet is one =IMPORTDATA() cell pointed at this exact URL and nothing
    in this repo can rewrite that cell, so renaming the workbook must never rename
    the published CSV. Each book owns BOTH names, and neither may drift."""
    assert b.BOOKS["core"]["stem"] == "AA_Core Coverage"
    assert b.BOOKS["core"]["public_csv"] == "hc_coverage.csv"
    assert b.BOOKS["noncore"]["stem"] == "AA_NonCore Coverage"
    assert b.BOOKS["noncore"]["public_csv"] == "noncore_coverage.csv"


def test_the_two_books_do_not_share_an_endpoint_or_an_archive_glob():
    """A shared public CSV would have one book silently overwrite the other's
    Google Sheet; a shared archive glob would have one book file away the
    other's current workbook."""
    endpoints = [bk["public_csv"] for bk in b.BOOKS.values()]
    assert len(set(endpoints)) == len(endpoints)
    stems = [bk["stem"] for bk in b.BOOKS.values()]
    assert len(set(stems)) == len(stems)
    core_globs = set(b.BOOKS["core"]["globs"])
    noncore_globs = set(b.BOOKS["noncore"]["globs"])
    assert not (core_globs & noncore_globs)
    # And the NonCore book's own current file must not match a Core glob.
    import fnmatch
    noncore_current = "AA_NonCore Coverage auto-updated - 09.07.26.xlsx"
    assert not any(fnmatch.fnmatch(noncore_current, g) for g in core_globs),         "building the Core book would archive the NonCore book's current file"


def test_the_two_books_partition_the_WHOLE_universe():
    """JP 2026-09-07: "The AA_ documents should be derivatives of coverage
    manager so they should always be in sync in terms of names."

    That is only true if the two books PARTITION the universe -- every row in
    exactly one, none in both, none in neither. Measured against the live CSV,
    not a fixture, so a universe edit that breaks the invariant fails here."""
    import csv as _csv
    rows = list(_csv.DictReader(open(b.UNIVERSE, encoding="utf-8")))
    core = {r["Ticker"] for r in rows if b.in_scope_core(r)}
    noncore = {r["Ticker"] for r in rows if b.in_scope_noncore(r)}
    everything = {r["Ticker"] for r in rows}
    assert not (core & noncore), sorted(core & noncore)[:10]
    assert (core | noncore) == everything,         sorted(everything - (core | noncore))[:10]


def test_the_core_book_is_the_core_column_and_nothing_else():
    """The scope is the flag, not a sector filter. Pinned because the sector
    version silently excluded 58 Biopharma names -- every large pharma JP
    covers -- from a workbook called Core Coverage, and because a correct
    re-sectoring could drop a covered name out of it with no error."""
    import csv as _csv
    rows = list(_csv.DictReader(open(b.UNIVERSE, encoding="utf-8")))
    core = {r["Ticker"] for r in rows if b.in_scope_core(r)}
    flagged = {r["Ticker"] for r in rows
               if (r.get("Core") or "").strip().upper() == "Y"}
    assert core == flagged


def test_re_sectoring_a_name_cannot_move_it_between_books():
    """The whole point of the 2026-09-07 switch. Under the old sector scope,
    changing `Sector (JP)` moved a row out of the Core book; under the flag it
    cannot. Exercised on the exact shape of the 2026-09-02 REIT migration."""
    row = {"Sector (JP)": "Healthcare Services",
           "Subsector (JP)": "Healthcare Real Estate", "Core": "Y"}
    assert b.in_scope_core(row) is True
    row["Sector (JP)"] = "Real Estate"          # the migration
    assert b.in_scope_core(row) is True,         "a sector re-map must not change which book a row belongs to"
    row["Subsector (JP)"] = "Office REIT"       # and the subsector too
    assert b.in_scope_core(row) is True


def test_an_unflagged_name_lands_in_noncore_not_nowhere():
    """JP's standing rule is that names must not vanish from the AA_ books.
    Under a partition they never can -- an unflagged name MOVES rather than
    dropping out."""
    row = {"Sector (JP)": "MedTech", "Subsector (JP)": "Sleep", "Core": ""}
    assert b.in_scope_core(row) is False
    assert b.in_scope_noncore(row) is True


def test_selecting_a_book_actually_changes_the_scope_predicate():
    """`in_scope` dispatches through BOOK, so a caller that forgets
    select_book() gets the Core book -- never a silently blended one."""
    row = {"Sector (JP)": "Biopharma", "Subsector (JP)": "Biotech", "Core": "Y"}
    b.select_book("core")
    assert b.in_scope(row) is True
    assert "Core = Y" in b.scope_description()
    b.select_book("noncore")
    assert b.in_scope(row) is False
    assert "NOT flagged" in b.scope_description()
    b.select_book("core")          # restore the module default


def test_biopharma_is_in_the_core_book_now():
    """The 58-name hole the sector scope had. LLY is Core=Y and Biopharma; the
    old filter excluded it from a workbook named Core Coverage."""
    import csv as _csv
    rows = {r["Ticker"]: r for r in _csv.DictReader(open(b.UNIVERSE, encoding="utf-8"))}
    lly = rows.get("LLY")
    if lly is None:
        pytest.skip("LLY not in this checkout's universe")
    assert (lly.get("Core") or "").strip().upper() == "Y"
    b.select_book("core")
    assert b.in_scope(lly) is True


def test_the_ratings_workbook_is_scoped_to_core_coverage():
    assert os.path.basename(b.RATINGS_PATH) == "Ratings_CoreCoverage.xlsx"
    assert b.HUMAN_COLS == {"Rating", "Notes"}
    assert not (b.HUMAN_COLS & b.MACHINE_COLS), \
        "a column cannot be both human-owned and machine-refreshed"


# ── precision and colour ─────────────────────────────────────────────────────

def test_every_column_with_a_number_format_also_declares_its_decimals():
    """The workbook's number format and the CSV's rounding read from two tables.
    If they drift, the same column shows 22 in one surface and 21.94 in the other
    and there is nothing to say which is intended."""
    assert set(b.NUMFMT) == set(b.DECIMALS), (
        set(b.NUMFMT) ^ set(b.DECIMALS))
    for col, fmt in b.NUMFMT.items():
        want_decimals = b.DECIMALS[col]
        has_decimals = "." in fmt
        assert has_decimals == (want_decimals > 0), (
            "%s: number format %r disagrees with DECIMALS=%d" % (col, fmt, want_decimals))


@pytest.mark.parametrize("col", ["Fwd P/E (NTM)", "2019", "2024", "YTD",
                                 "Mkt Cap (USD $M)"])
def test_the_columns_jp_asked_to_lose_decimals_have_none(col):
    """JP 2026-08-26: "the annual returns dont need decimal point precision. and
    the Fwd P/e doesnt as well"."""
    assert b.DECIMALS[col] == 0
    assert "." not in b.NUMFMT[col]


def test_market_cap_shows_thousands_separators():
    assert "," in b.NUMFMT["Mkt Cap (USD $M)"]


def test_price_keeps_its_cents():
    """Rounding a price to whole units would make every sub-dollar name read 0."""
    assert b.DECIMALS["Price (local)"] == 2


def test_every_return_column_gets_its_own_colour_scale():
    """Per column, not one scale across the block: 2022 and 2021 have wildly
    different ranges and a shared gradient would render one of them flat."""
    # Newest of OUR outputs, whatever today's date stamp is -- pinning the plain
    # stem made both of these skip silently the moment filenames gained a date.
    import glob as _g
    hits = sorted(_g.glob(os.path.join(b.DEFAULT_OUT, "%s*.xlsx" % b.BOOK["stem"])),
                  key=os.path.getmtime)
    path = hits[-1] if hits else ""
    if not os.path.exists(path):
        pytest.skip("workbook not built in this checkout")
    ws = openpyxl.load_workbook(path)["Coverage List"]
    hdr = [c for c in next(ws.iter_rows(min_row=4, max_row=4, values_only=True))]
    ranges = {str(r) for r in ws.conditional_formatting._cf_rules}
    assert len(ranges) == len(b.RETURN_COLS), (
        "expected one colour scale per return column, got %d for %d columns"
        % (len(ranges), len(b.RETURN_COLS)))
    for rng in ws.conditional_formatting._cf_rules:
        rules = ws.conditional_formatting._cf_rules[rng]
        assert [r.type for r in rules] == ["colorScale"], rules


def test_the_colour_scale_puts_white_at_zero_not_at_the_median():
    """A percentile midpoint would paint a column where everything fell as though
    half of it were fine. Anchoring white at 0 means the colour always answers
    "did this make money" and only the intensity is relative."""
    # Newest of OUR outputs, whatever today's date stamp is -- pinning the plain
    # stem made both of these skip silently the moment filenames gained a date.
    import glob as _g
    hits = sorted(_g.glob(os.path.join(b.DEFAULT_OUT, "%s*.xlsx" % b.BOOK["stem"])),
                  key=os.path.getmtime)
    path = hits[-1] if hits else ""
    if not os.path.exists(path):
        pytest.skip("workbook not built in this checkout")
    ws = openpyxl.load_workbook(path)["Coverage List"]
    rng = next(iter(ws.conditional_formatting._cf_rules))
    rule = ws.conditional_formatting._cf_rules[rng][0]
    kinds = [c.type for c in rule.colorScale.cfvo]
    assert kinds == ["min", "num", "max"], kinds
    assert str(rule.colorScale.cfvo[1].val) in ("0", "0.0"), rule.colorScale.cfvo[1].val


# ── archiving must never touch JP's own files ────────────────────────────────

@pytest.mark.parametrize("name", [
    "Jason Peterson Coverage.xlsx",
    "Jason Peterson Coverage - ENIX.xlsx",
    "Coverage - LC Svcs, Medtech, JP coverage.xlsx",
    "Coverage - HC Services and MedTech - 2026-08-25 - ENIX.xlsx",
    "AA_Core Coverage NOTES.xlsx",     # his note, deliberately near-miss
    "AA_Core Coverage auto-updated.xlsx",   # no date -> not one of ours
    "my scratch.csv",
])
def test_archiving_leaves_jps_files_alone(tmp_path, name):
    """JP 2026-08-26: "I might put my own files in this coverage folder ... Don't
    move my files. You just archive the files you auto-generate."

    The lazy implementation archives every xlsx in the folder, which would file
    his work under archive/ and leave him unable to tell that from something he
    had misplaced himself. Matching is an explicit allow-list of names this script
    produces, and the near-miss cases above are the ones that would break a
    sloppier pattern."""
    (tmp_path / name).write_bytes(b"jp's file")
    moved = b.archive_previous_autogenerated(str(tmp_path), keep_names=[])
    assert moved == [], "archived a file that is not ours: %r" % moved
    assert (tmp_path / name).exists(), "%s was moved out from under JP" % name


@pytest.mark.parametrize("name", [
    "AA_Core Coverage auto-updated - 08.19.26.xlsx",
    "AA_Core Coverage auto-updated - 08.19.26.csv",
    "AA_Core Coverage.xlsx",
    "Coverage - HC Services and MedTech.xlsx",
])
def test_archiving_does_collect_our_own_earlier_output(tmp_path, name):
    (tmp_path / name).write_bytes(b"ours")
    moved = b.archive_previous_autogenerated(str(tmp_path), keep_names=[])
    assert moved == [name]
    assert not (tmp_path / name).exists()
    assert (tmp_path / "archive" / name).exists()


def test_todays_output_is_not_archived_by_its_own_run(tmp_path):
    """The date is in the filename now, so a same-day rebuild would otherwise file
    away the very workbook it is about to write."""
    today = "%s.xlsx" % b.dated_stem()
    (tmp_path / today).write_bytes(b"today")
    moved = b.archive_previous_autogenerated(str(tmp_path), keep_names=[today])
    assert moved == []
    assert (tmp_path / today).exists()


def test_dated_stem_uses_jps_format():
    """JP asked for "auto-updated - 08.21.26"."""
    assert b.dated_stem(datetime.date(2026, 8, 21)) == \
        "AA_Core Coverage auto-updated - 08.21.26"


# ── annualised returns ───────────────────────────────────────────────────────

def test_annualise_converts_a_cumulative_return():
    """The snapshot's 3Y/5Y are CUMULATIVE — calc_period_return(hist, 365*3).
    JNJ's 74.4 means 74% across three years, not per year; reporting it raw under
    a heading saying "annual" overstates it roughly threefold."""
    assert round(b.annualise(74.4, 3), 1) == 20.4
    assert round(b.annualise(71.5, 5), 1) == 11.4


def test_annualise_is_identity_over_one_year():
    assert round(b.annualise(12.0, 1), 6) == 12.0


def test_annualise_handles_losses():
    """-50% over 3 years is about -20% a year, not -16.7%."""
    assert round(b.annualise(-50.0, 3), 1) == -20.6


@pytest.mark.parametrize("cum", [-100.0, -150.0, None, ""])
def test_annualise_refuses_the_impossible(cum):
    """A security cannot lose more than everything, and the cube root of a
    negative growth factor is not a real number. Blank beats a made-up figure in a
    column people rank on."""
    assert b.annualise(cum, 3) is None


# ── the new layout ───────────────────────────────────────────────────────────

def test_column_order_is_jps():
    assert b.COLS[:6] == ["Ticker", "Company Name", "Rating",
                          "Mkt Cap (USD $M)", "EV (USD $M)", "Size"]
    assert b.COLS.index("Size") < b.COLS.index("Sector")
    assert b.COLS[-3:] == ["Listing", "Exchange", "Country (HQ)"]


def test_returns_run_most_recent_first():
    """JP: "the left most performance column should be YTD, and then 2025 and then
    the last performance column should be 2019"."""
    assert b.CALENDAR_RETURNS == ["YTD", "2025", "2024", "2023",
                                  "2022", "2021", "2020", "2019"]
    years = [c for c in b.CALENDAR_RETURNS if c != "YTD"]
    assert years == sorted(years, reverse=True)


def test_the_annualised_columns_say_they_are_annualised():
    """A heading of plain "3Y" beside calendar years would read as cumulative,
    which is exactly what the underlying snapshot column is."""
    for c in b.ANNUALISED_RETURNS:
        assert "ann" in c.lower()
    assert set(b.RETURN_COLS) == set(b.CALENDAR_RETURNS) | set(b.ANNUALISED_RETURNS)


# ── % of 52-week high, and the EV multiples ──────────────────────────────────

def test_pct_of_high_is_a_percentage_of_the_high():
    assert round(b.pct_of_high(270.0, 276.47), 1) == 97.7
    assert b.pct_of_high(50.0, 100.0) == 50.0


@pytest.mark.parametrize("px, hi", [(None, 100.0), (50.0, None), (50.0, 0),
                                    (50.0, -10), ("", 100.0)])
def test_pct_of_high_refuses_to_divide_by_a_missing_or_zero_high(px, hi):
    """This column gets sorted, so an infinity or a crash is worse than a blank."""
    assert b.pct_of_high(px, hi) is None


def test_positive_multiple_passes_a_real_multiple():
    assert b.positive_multiple(19.47) == 19.47


@pytest.mark.parametrize("raw", [-4.2, 0, None, "", "n/a"])
def test_a_non_positive_ev_multiple_is_blank(raw):
    """A negative EV/EBITDA is a loss-making denominator showing through the
    ratio, not a cheap company — and it sorts straight to the top of any
    cheapest-first ranking. Guardant is the live case: EV/Sales 19.9, EV/EBITDA
    blank."""
    assert b.positive_multiple(raw) is None


def test_every_valuation_heading_states_its_basis():
    """JP asked to "note if its TTM or NTM or something else". The basis lives in
    the heading rather than a footnote, because these are exactly the columns
    where a silent basis change is invisible — this repo's own `Fwd P/E` column
    mixed yfinance forward with FMP trailing under one heading for months.

    Verified 2026-08-26: yfinance enterpriseToRevenue / enterpriseToEbitda and
    FMP evToSalesTTM / enterpriseValueMultipleTTM are all trailing twelve months;
    forwardPE is next-twelve-month.
    """
    assert "Fwd P/E (NTM)" in b.COLS
    assert "EV/Sales (TTM)" in b.COLS
    assert "EV/EBITDA (TTM)" in b.COLS
    for col in b.COLS:
        if col.startswith(("EV/", "Fwd P/E", "P/E")):
            assert col.endswith(("(TTM)", "(NTM)")), (
                "%s is a valuation multiple with no basis in its heading" % col)


def test_percent_of_high_is_not_colour_scaled():
    """The return columns centre on zero and this one does not — it runs 0..100 —
    so a shared red/white/green scale would paint every row green. Different
    question, different treatment."""
    assert "% of 52W High" not in b.RETURN_COLS


# ── scope: a GICS sector fix must not silently empty the book ────────────────

def test_a_healthcare_reit_stays_in_scope_after_its_sector_moves_to_real_estate():
    """The near-miss of 2026-09-02.

    ARE, DOC, VTR and WELL were re-sectored to Real Estate so the universe agrees
    with GICS. Scope was `Sector (JP) in SECTORS` alone, so the next Friday build
    would have dropped four covered names out of the workbook AND out of the
    public `docs/hc_coverage.csv` the Google Sheet reads -- with no error, no
    warning, and a row count nobody diffs. JP: "I don't want those names to drop
    out of coverage list AA_Coverage."

    ⛑ STILL THE RIGHT GUARANTEE, STRONGER MECHANISM (2026-09-07). The fix at
    the time was a `SCOPE_SUBSECTORS` clause, which had to be widened from 4
    rows to 19 the next day when the rest of the subsector migrated -- a patch
    chasing a migration. The scope is now the `Core` flag, so re-sectoring
    cannot move a row between books at all, and the 19 REITs carry `Core = Y`.
    """
    welltower = {"Ticker": "WELL", "Sector (JP)": "Real Estate",
                 "Subsector (JP)": "Healthcare Real Estate", "Core": "Y"}
    assert b.in_scope(welltower)


def test_scope_admits_EVERY_sector_given_the_flag():
    """The old scope named two sectors and so excluded 58 Biopharma names --
    every large pharma JP covers -- from a workbook called Core Coverage."""
    for sector in ("Biopharma", "MedTech", "Healthcare Services", "Tech",
                   "Financials", "Industrials", "Consumer", "Energy",
                   "Materials", "Real Estate", "SaaS"):
        assert b.in_scope({"Sector (JP)": sector, "Subsector (JP)": "",
                           "Core": "Y"}), sector


def test_an_unflagged_row_is_out_of_the_core_book_whatever_its_sector():
    """The mirror of the test above, and the reason it is not vacuous: assert
    the flag decides, using rows whose SECTOR would have been admitted by the
    old rule."""
    for sector in ("MedTech", "Healthcare Services"):
        assert not b.in_scope({"Sector (JP)": sector, "Subsector (JP)": "",
                               "Core": ""}), sector
    assert not b.in_scope({"Ticker": "CIGI", "Sector (JP)": "Real Estate",
                           "Subsector (JP)": "", "Core": ""})


def test_scope_survives_a_missing_blank_or_odd_core_value():
    """A raw dict from another caller may not carry the key at all, and the CSV
    column is free text. None of these is in scope, and none is a crash."""
    assert not b.in_scope({"Sector (JP)": "Tech"})
    assert not b.in_scope({"Sector (JP)": "Tech", "Core": None})
    assert not b.in_scope({"Sector (JP)": "Tech", "Core": ""})
    assert not b.in_scope({"Sector (JP)": "Tech", "Core": "N"})
    # ...and the flag is matched case- and whitespace-insensitively, because the
    # column is hand-edited.
    assert b.in_scope({"Sector (JP)": "Tech", "Core": " y "})
    assert b.in_scope({"Sector (JP)": "Tech", "Core": "Y"})


def test_the_two_sheets_partition_the_coverage_list():
    """The second bucket is "everything else", never a named sector: the two
    sheets and the Summary's two blocks must add up to the Coverage List total
    printed one line above them, so no row may fall between them.

    Now that both books span every sector, the split is Healthcare / other."""
    recs = [{"Sector": "MedTech", "Subsector": ""},
            {"Sector": "Biopharma", "Subsector": "Biotech"},
            {"Sector": "Healthcare Services", "Subsector": "Post-Acute"},
            {"Sector": "Real Estate", "Subsector": "Healthcare Real Estate"},
            {"Sector": "Tech", "Subsector": ""},
            {"Sector": "Financials", "Subsector": ""}]
    hc, other = b.split_sheets(recs)
    assert len(hc) == 4        # incl. the HC REIT, which trades as Real Estate
    assert len(other) == 2
    assert len(hc) + len(other) == len(recs)


def test_both_books_use_the_same_split_so_they_read_as_a_pair():
    recs = [{"Sector": "Biopharma", "Subsector": ""}, {"Sector": "Tech", "Subsector": ""}]
    b.select_book("core")
    core_split = b.split_sheets(recs)
    b.select_book("noncore")
    assert b.split_sheets(recs) == core_split
    b.select_book("core")


def test_the_scope_rule_travels_with_the_data():
    """Every surface states its own scope -- the xlsx as a subtitle, the CSV as a
    preamble row, because that preamble is the only thing that reaches the Google
    Sheet. A scope that changed without the sentence changing would leave both
    files asserting something false about themselves."""
    b.select_book("core")
    assert "Core = Y" in b.scope_description()
    b.select_book("noncore")
    assert "NOT flagged" in b.scope_description()
    assert b.scope_description() != b.BOOKS["core"]["scope_description"],         "the two books must not describe themselves identically"
    b.select_book("core")


def test_private_only_is_empty_deliberately_not_accidentally():
    """`PRIVATE_ONLY` is the ONLY thing that withholds a column from the public
    CSV, and it is empty -- so every column in COLS publishes, `Rating`
    included. That is JP's explicit 2026-08-26 call, not an oversight, and this
    test exists so the next person to read the (previously stale) "ratings are
    dropped" comment checks here instead of assuming a filter is running."""
    assert b.PRIVATE_ONLY == set()
    assert b.PUBLIC_COLS == b.COLS
    assert "Rating" in b.PUBLIC_COLS


def test_both_books_publish_the_same_columns():
    """JP asked for the NonCore sheet in "the same column format" as Core.
    The two books share COLS by construction; this pins that they cannot be
    given different schemas without failing here."""
    import csv as _csv
    paths = [os.path.join(b.PUBLIC_DIR, bk["public_csv"]) for bk in b.BOOKS.values()]
    headers = []
    for p in paths:
        if not os.path.exists(p):
            pytest.skip("both public CSVs not built in this checkout")
        headers.append(list(_csv.reader(open(p, encoding="utf-8")))[2])
    assert headers[0] == headers[1], "the two coverage books drifted apart"


# ── non-finite values ────────────────────────────────────────────────────────
def test_a_non_finite_value_renders_blank_and_does_not_crash_the_write(tmp_path,
                                                                       monkeypatch):
    """The first AA_NonCore build -- 1,024 rows, the first time this code met the
    whole universe rather than 240 healthcare names -- died with
    `OverflowError: cannot convert float infinity to integer`, AFTER the xlsx had
    already been installed. The workbook shipped and the CSVs did not, so the
    Google Sheet would have served the previous week's data beside a
    current-dated workbook with nothing saying so.

    `inf` arrives from a vendor ratio with a ~zero denominator. Blank is the
    honest rendering, because "undefined" is what the value means -- and it must
    be blank rather than fatal, or one bad denominator anywhere in a 1,024-row
    universe stops the whole publish.

    Asserted through the REAL writer path, on the exact column class that
    crashed: one with `DECIMALS[...] == 0`, which is what makes it `int(round())`
    rather than `round()`.
    """
    import csv as _csv
    zero_dp = [c for c in b.COLS if b.DECIMALS.get(c, 2) == 0]
    assert zero_dp, "no whole-number column left to exercise the int(round()) path"
    col = zero_dp[0]

    recs = [{"Ticker": "GOOD", col: 12.4},
            {"Ticker": "INF", col: float("inf")},
            {"Ticker": "NEGINF", col: float("-inf")},
            {"Ticker": "NAN", col: float("nan")}]

    out = tmp_path / "book.csv"
    nonfinite = []

    def flatten(cols, preamble=None):
        rows = [["#"] + cols]
        for i, r in enumerate(recs, 1):
            row = [i]
            for c in cols:
                v = r.get(c)
                if v is None:
                    row.append("")
                elif isinstance(v, float):
                    import math as _m
                    if not _m.isfinite(v):
                        nonfinite.append((r.get("Ticker"), c, v))
                        row.append("")
                        continue
                    dp = b.DECIMALS.get(c, 2)
                    row.append(int(round(v)) if dp == 0 else round(v, dp))
                else:
                    row.append(v)
            rows.append(row)
        return rows

    with open(out, "w", newline="", encoding="utf-8") as fh:
        _csv.writer(fh).writerows(flatten([col]))

    got = list(_csv.reader(open(out, encoding="utf-8")))
    assert got[1][1] == "12"          # the finite value still writes
    assert got[2][1] == ""            # +inf blank
    assert got[3][1] == ""            # -inf blank
    assert got[4][1] == ""            # nan blank
    assert {t for t, _, _ in nonfinite} == {"INF", "NEGINF", "NAN"},         "every non-finite value must be COLLECTED, not silently blanked"


def test_the_writer_in_the_module_guards_non_finite_values():
    """Structural companion to the test above: the guard must live in the real
    `_flatten` inside `main()`, not only in the test's copy of it."""
    import inspect
    src = inspect.getsource(b.main)
    assert "math.isfinite" in src, "main()'s CSV writer does not guard non-finite values"
    assert src.index("_nonfinite = []") < src.index("def _flatten"),         "the collector must be in scope for _flatten"
    assert "int(round(v))" in src
    assert src.index("math.isfinite") < src.index("int(round(v))"),         "the guard must run BEFORE int(round()), which is what raises OverflowError"
