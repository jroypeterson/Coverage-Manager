"""Tests for universe/crsp_snapshot.py — no live CRSP calls."""

import csv
import json
import urllib.error

import pytest

from universe import crsp_snapshot as cs

# ── fixtures ─────────────────────────────────────────────────────────────────

HEADER = ["TradeDate", "Index Ticker", "Index Name", "Ticker", "Company", "Weight"]


def _rows(trade_date="03/31/2026", n_total=2600, extra=()):
    """A synthetic constituents file big enough to clear the sanity floor."""
    out = []
    w = 1.0 / n_total
    for i in range(n_total):
        out.append({
            "TradeDate": trade_date, "Index Ticker": "CRSPTM1",
            "Index Name": "Total Market", "Ticker": f"T{i:04d}",
            "Company": f"COMPANY {i} INC", "Weight": f"{w:.12f}",
        })
    out.extend(extra)
    return out


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=HEADER)
        wr.writeheader()
        wr.writerows(rows)
    return path


# ── date normalisation ───────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,want", [
    ("03/31/2026", "2026-03-31"),
    ("3/31/2026", "2026-03-31"),
    ("12/1/2025", "2025-12-01"),
    ("2026-03-31", "2026-03-31"),
])
def test_iso_date(raw, want):
    assert cs._iso_date(raw) == want


def test_iso_date_rejects_garbage():
    with pytest.raises(ValueError):
        cs._iso_date("March 31 2026")


# ── verification ─────────────────────────────────────────────────────────────


def test_verify_happy_path(tmp_path):
    p = _write(tmp_path / "c.csv", _rows())
    trade_date, tm, warnings = cs.verify_total_market(cs.parse_constituents(p))
    assert trade_date == "2026-03-31"
    assert len(tm) == 2600
    assert warnings == []


def test_verify_rejects_missing_total_market(tmp_path):
    """The index is keyed under CRSPTM1; a file with only CRSPTMT must not
    silently yield an empty universe."""
    rows = [dict(r, **{"Index Ticker": "CRSPTMT"}) for r in _rows()]
    p = _write(tmp_path / "c.csv", rows)
    with pytest.raises(ValueError, match="no CRSPTM1 rows"):
        cs.verify_total_market(cs.parse_constituents(p))


def test_verify_rejects_truncated_file(tmp_path):
    """A dead CDN URL commonly serves a short HTML page with HTTP 200."""
    p = _write(tmp_path / "c.csv", _rows(n_total=12))
    with pytest.raises(ValueError, match="outside sane range"):
        cs.verify_total_market(cs.parse_constituents(p))


def test_verify_rejects_schema_change(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("Date,Symbol\n2026-03-31,AAPL\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        cs.parse_constituents(p)


def test_verify_warns_on_bad_weight_sum(tmp_path):
    rows = _rows()
    for r in rows[:200]:
        r["Weight"] = "0.0"
    p = _write(tmp_path / "c.csv", rows)
    _, _, warnings = cs.verify_total_market(cs.parse_constituents(p))
    assert any("weights sum" in w for w in warnings)


def test_verify_rejects_multiple_trade_dates(tmp_path):
    rows = _rows()
    rows[0]["TradeDate"] = "12/31/2025"
    p = _write(tmp_path / "c.csv", rows)
    with pytest.raises(ValueError, match="expected one TradeDate"):
        cs.verify_total_market(cs.parse_constituents(p))


# ── classification ───────────────────────────────────────────────────────────


def test_classification_derives_sector_size_style():
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPTM1", "Index Name": "Total Market",
         "Ticker": "AAPL", "Company": "APPLE INC COM", "Weight": "0.05"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPIT1", "Index Name": "Technology",
         "Ticker": "AAPL", "Company": "APPLE INC COM", "Weight": "0.10"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPME1", "Index Name": "Mega Cap",
         "Ticker": "AAPL", "Company": "APPLE INC COM", "Weight": "0.08"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPMEG1", "Index Name": "Mega Growth",
         "Ticker": "AAPL", "Company": "APPLE INC COM", "Weight": "0.12"},
    ]
    c = cs.build_classification(rows)
    assert c["AAPL"] == {"sector": "Technology", "size": "Mega", "style": "Growth"}


def test_classification_records_split_style_as_both():
    """CRSP splits 134 names across the growth and value boxes with partial
    weight in each. That is a real state, not a conflict to arbitrate."""
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPTM1", "Index Name": "Total Market",
         "Ticker": "SPLT", "Company": "SPLIT CO", "Weight": "0.001"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPLCG1", "Index Name": "Large Growth",
         "Ticker": "SPLT", "Company": "SPLIT CO", "Weight": "0.002"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPLCV1", "Index Name": "Large Value",
         "Ticker": "SPLT", "Company": "SPLIT CO", "Weight": "0.002"},
    ]
    assert cs.build_classification(rows)["SPLT"]["style"] == "Growth+Value"


def test_classification_style_is_axis_not_box():
    """Style boxes are not nested — Mega Growth is not a subset of Large Growth
    — so no single box is the right label. The axis is."""
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPMEG1", "Index Name": "Mega Growth",
         "Ticker": "NVDA", "Company": "NVIDIA CORP COM", "Weight": "0.1"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPLCG1", "Index Name": "Large Growth",
         "Ticker": "NVDA", "Company": "NVIDIA CORP COM", "Weight": "0.1"},
    ]
    assert cs.build_classification(rows)["NVDA"]["style"] == "Growth"


@pytest.mark.parametrize("order", [
    ["CRSPME1", "CRSPMI1"],
    ["CRSPMI1", "CRSPME1"],
])
def test_classification_resolves_packeting_straddles(order):
    """CRSP's packeting rule parks a migrating name in two adjacent tiers. The
    larger tier must win, and the answer must not depend on row order."""
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": idx, "Index Name": idx,
         "Ticker": "MIGR", "Company": "MIGRATING CO", "Weight": "0.001"}
        for idx in order
    ]
    assert cs.build_classification(rows)["MIGR"]["size"] == "Mega"


def test_classification_ignores_composite_size_indexes():
    """`Large Cap` is Mega union Mid, not a tier. Treating it as one labelled
    every mid-cap 'Large' and erased Mega from the index entirely."""
    assert "CRSPLC1" not in cs.SIZE_INDEXES
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPMI1", "Index Name": "Mid Cap",
         "Ticker": "MIDCO", "Company": "MID CO", "Weight": "0.001"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPLC1", "Index Name": "Large Cap",
         "Ticker": "MIDCO", "Company": "MID CO", "Weight": "0.001"},
    ]
    assert cs.build_classification(rows)["MIDCO"]["size"] == "Mid"


def test_classification_leaves_microcaps_unlabelled():
    """Sector indexes cover Core Cap only. An unlabelled name must stay None so
    'no sector published' cannot be mistaken for a real sector value."""
    rows = [
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPTM1", "Index Name": "Total Market",
         "Ticker": "TINY", "Company": "TINY CO", "Weight": "0.0000001"},
        {"TradeDate": "03/31/2026", "Index Ticker": "CRSPMC1", "Index Name": "Micro Cap",
         "Ticker": "TINY", "Company": "TINY CO", "Weight": "0.001"},
    ]
    c = cs.build_classification(rows)
    assert c["TINY"]["sector"] is None
    assert c["TINY"]["size"] == "Micro"


# ── diff ─────────────────────────────────────────────────────────────────────


def test_diff_constituents():
    prior = [{"Ticker": "AAA"}, {"Ticker": "BBB"}, {"Ticker": "CCC"}]
    current = [{"Ticker": "BBB"}, {"Ticker": "CCC"}, {"Ticker": "DDD"}]
    added, dropped = cs.diff_constituents(prior, current)
    assert added == ["DDD"]
    assert dropped == ["AAA"]


def test_find_prior_snapshot_ignores_same_and_future(tmp_path):
    for d in ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"):
        (tmp_path / f"constituents_{d}.csv").write_text("x", encoding="utf-8")
    prior = cs.find_prior_snapshot("2026-03-31", tmp_path)
    assert prior is not None and prior.stem.endswith("2025-12-31")


def test_find_prior_snapshot_none_when_empty(tmp_path):
    assert cs.find_prior_snapshot("2026-03-31", tmp_path) is None


# ── reconciliation ───────────────────────────────────────────────────────────


def test_reconcile_separates_absence_from_mismatch():
    tm = [
        {"Ticker": "UCB", "Company": "UNITED CMNTY BKS BLAIRSVLE GA"},
        {"Ticker": "LLY", "Company": "LILLY ELI & CO COM"},
    ]
    universe = [
        # ticker collides with a live US listing -> real finding
        {"Ticker": "UCB", "Company Name": "UCB SA", "Country (HQ)": "Belgium"},
        # cosmetic ordering difference -> must NOT flag
        {"Ticker": "LLY", "Company Name": "Eli Lilly And Co", "Country (HQ)": "United States"},
        # foreign ADR CRSP does not carry -> absent, not a mismatch
        {"Ticker": "ASML", "Company Name": "ASML Holding NV", "Country (HQ)": "Netherlands"},
    ]
    r = cs.reconcile_universe(tm, universe)
    assert r.checked == 3
    assert r.matched == 2
    assert [a["ticker"] for a in r.absent] == ["ASML"]
    assert [m["ticker"] for m in r.name_mismatches] == ["UCB"]


def test_reconcile_flags_foreign_hq_symbol_collision():
    """The structural check must catch what fuzzy names miss: Swiss Medartis and
    US Medifast score 0.63 on spelling alone, above any usable threshold."""
    tm = [{"Ticker": "MED", "Company": "MEDIFAST INC COM"}]
    universe = [{
        "Ticker": "MED", "Company Name": "Medartis Holding AG",
        "Country (HQ)": "Switzerland", "Exchange Code": "SWX",
    }]
    r = cs.reconcile_universe(tm, universe)
    assert [c["ticker"] for c in r.symbol_collisions] == ["MED"]


def test_reconcile_ignores_us_hq_and_suffixed_symbols():
    """A US company is supposed to be in CRSP, and a suffixed foreign line
    (`ROG.SW`) can't be confused with a US symbol by any consumer."""
    tm = [{"Ticker": "AAPL", "Company": "APPLE INC COM"},
          {"Ticker": "ROG", "Company": "ROGERS CORP"}]
    universe = [
        {"Ticker": "AAPL", "Company Name": "Apple Inc", "Country (HQ)": "United States"},
        {"Ticker": "ROG.SW", "Company Name": "Roche Holding AG", "Country (HQ)": "Switzerland"},
    ]
    r = cs.reconcile_universe(tm, universe)
    assert r.symbol_collisions == []


def test_reconcile_ignores_foreign_domiciled_inversions():
    """Irish/UK inversions are foreign-HQ and legitimately in CRSP under
    matching names. Domicile alone must not flag them."""
    tm = [
        {"Ticker": "MDT", "Company": "MEDTRONIC PLC"},
        {"Ticker": "LIN", "Company": "LINDE PLC"},
        {"Ticker": "TEAM", "Company": "ATLASSIAN CORPORATION CLASS A"},
    ]
    universe = [
        {"Ticker": "MDT", "Company Name": "Medtronic plc", "Country (HQ)": "Ireland"},
        {"Ticker": "LIN", "Company Name": "Linde plc", "Country (HQ)": "United Kingdom"},
        {"Ticker": "TEAM", "Company Name": "Atlassian Corporation", "Country (HQ)": "Australia"},
    ]
    r = cs.reconcile_universe(tm, universe)
    assert r.symbol_collisions == []


@pytest.mark.parametrize("cm_name,crsp_name", [
    ("Kiniksa Pharmaceuticals Internationl PLC", "KINIKSA PHARMA LTD CL A"),
    ("Mettler-Toledo International Inc", "METTLER TOLEDO INTER COM"),
    ("Bio-Rad Laboratories, Inc.", "BIO RAD LABS INC CL A"),
])
def test_name_similarity_handles_crsp_truncation(cm_name, crsp_name):
    """CRSP truncates words rather than dropping them; exact-token overlap alone
    scores these same-company pairs low enough to look like collisions."""
    assert cs._name_similarity(cm_name, crsp_name) >= 0.70


def test_name_similarity_returns_none_when_uncomparable():
    assert cs._name_similarity("", "MEDIFAST INC COM") is None
    assert cs._name_similarity("Inc Corp Ltd", "MEDIFAST INC COM") is None


def test_reconcile_tolerates_crsp_name_conventions():
    """CRSP writes names surname-first and abbreviated. These are the same
    companies and must not be reported."""
    tm = [
        {"Ticker": "LLY", "Company": "LILLY ELI & CO COM"},
        {"Ticker": "HSIC", "Company": "SCHEIN HENRY INC COM"},
        {"Ticker": "COO", "Company": "COOPER COS INC"},
        {"Ticker": "BIO", "Company": "BIO RAD LABS INC CL A"},
    ]
    universe = [
        {"Ticker": "LLY", "Company Name": "Eli Lilly And Co", "Country (HQ)": "United States"},
        {"Ticker": "HSIC", "Company Name": "Henry Schein, Inc.", "Country (HQ)": "United States"},
        {"Ticker": "COO", "Company Name": "The Cooper Companies, Inc.", "Country (HQ)": "United States"},
        {"Ticker": "BIO", "Company Name": "Bio-Rad Laboratories, Inc. Class A Common Stock",
         "Country (HQ)": "United States"},
    ]
    r = cs.reconcile_universe(tm, universe)
    assert r.name_mismatches == []


def test_reconcile_skips_rows_without_a_name():
    """A blank name yields no comparison, so it must produce no finding —
    an absent name is not evidence of a mismatch."""
    tm = [{"Ticker": "AAA", "Company": "ALPHA CORP"}]
    r = cs.reconcile_universe(tm, [{"Ticker": "AAA", "Company Name": ""}])
    assert r.matched == 1
    assert r.name_mismatches == []


# ── snapshot orchestration (network stubbed) ─────────────────────────────────


@pytest.fixture
def stub_download(monkeypatch):
    """Replace the network fetch with a local copy of `payload`."""
    state = {"payload": None, "fail": False}

    def fake(url, dest):
        if state["fail"]:
            raise RuntimeError("boom")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write(dest, state["payload"])

    monkeypatch.setattr(cs, "_download", fake)
    return state


def test_snapshot_archives_and_writes_classification(tmp_path, stub_download):
    stub_download["payload"] = _rows()
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "ok"
    assert r.trade_date == "2026-03-31"
    assert (tmp_path / "constituents_2026-03-31.csv").exists()
    payload = json.loads((tmp_path / "classification_2026-03-31.json").read_text())
    assert payload["trade_date"] == "2026-03-31"


def test_snapshot_is_idempotent(tmp_path, stub_download):
    stub_download["payload"] = _rows()
    cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    second = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert second.status == "unchanged"
    assert second.ok


def test_snapshot_reports_delta_against_prior_quarter(tmp_path, stub_download):
    stub_download["payload"] = _rows(trade_date="12/31/2025")
    cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)

    nxt = _rows(trade_date="03/31/2026")
    nxt.pop()  # one name drops out
    nxt.append({
        "TradeDate": "03/31/2026", "Index Ticker": "CRSPTM1",
        "Index Name": "Total Market", "Ticker": "NEWCO",
        "Company": "NEW CO INC", "Weight": "0.0001",
    })
    stub_download["payload"] = nxt

    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "ok"
    assert r.prior_trade_date == "2025-12-31"
    assert r.added == ["NEWCO"]
    assert len(r.dropped) == 1


def test_snapshot_download_failure_is_loud_and_writes_nothing(tmp_path, stub_download):
    stub_download["fail"] = True
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "failed"
    assert not r.ok
    assert r.errors and "download failed" in r.errors[0]
    assert not list(tmp_path.glob("constituents_*.csv"))


def test_snapshot_bad_payload_leaves_no_partial_file(tmp_path, stub_download):
    """Verification failure must not leave a staging file behind that a later
    run could mistake for a real snapshot."""
    stub_download["payload"] = _rows(n_total=5)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "failed"
    assert not list(tmp_path.glob("*.csv"))


def test_snapshot_dry_run_writes_nothing(tmp_path, stub_download):
    stub_download["payload"] = _rows()
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True, dry_run=True)
    assert r.status == "skipped (dry run)"
    assert r.constituent_count == 2600
    assert not list(tmp_path.glob("constituents_*.csv"))


def _levels_csv(path, dates):
    path.write_text(
        "Date,Name,Ticker,Level\n"
        + "".join(f"{d},Total Market,CRSPTMT,{1000 + i}\n" for i, d in enumerate(dates)),
        encoding="utf-8",
    )
    return path


def test_levels_archive_named_by_content_not_download_date(tmp_path):
    lv = _levels_csv(tmp_path / "index_levels.csv", ["2026-07-25", "2026-07-27", "2026-07-26"])
    out = cs._archive_levels(lv, tmp_path)
    assert out is not None and out.name == "index_levels_2026-07-27.csv.gz"
    assert out.parent.name == "archive"


def test_levels_archive_is_readable_gzip(tmp_path):
    import gzip

    lv = _levels_csv(tmp_path / "index_levels.csv", ["2026-07-27"])
    out = cs._archive_levels(lv, tmp_path)
    assert gzip.open(out, "rt", encoding="utf-8").read() == lv.read_text(encoding="utf-8")


def test_levels_archive_is_idempotent(tmp_path):
    """A re-run, a retry, or a stale upstream file must not mint a second copy
    claiming to be newer data."""
    lv = _levels_csv(tmp_path / "index_levels.csv", ["2026-07-27"])
    assert cs._archive_levels(lv, tmp_path) is not None
    assert cs._archive_levels(lv, tmp_path) is None
    assert len(list((tmp_path / "archive").glob("*.csv.gz"))) == 1


def test_levels_archive_handles_crsp_slash_dates(tmp_path):
    lv = _levels_csv(tmp_path / "index_levels.csv", ["07/27/2026", "07/25/2026"])
    assert cs._archive_levels(lv, tmp_path).name == "index_levels_2026-07-27.csv.gz"


def test_levels_archive_rejects_a_file_with_no_dates(tmp_path):
    p = tmp_path / "index_levels.csv"
    p.write_text("Date,Name,Ticker,Level\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no dates"):
        cs._archive_levels(p, tmp_path)


def test_snapshot_archives_levels_on_a_new_quarter(tmp_path, monkeypatch):
    """The working copy is refreshed in place; a dated copy is kept when a new
    quarter lands, so a restatement or a dead URL cannot erase the history."""
    payload = _rows()

    def fake(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "index_levels" in url or "daily-index-levels" in url:
            _levels_csv(dest, ["2026-07-27"])
        else:
            _write(dest, payload)

    monkeypatch.setattr(cs, "_download", fake)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=False)
    assert r.status == "ok"
    assert r.levels_archive is not None
    assert (tmp_path / "index_levels.csv").exists()          # working copy
    assert (tmp_path / "archive" / "index_levels_2026-07-27.csv.gz").exists()

    # Second run, same quarter: refreshed in place, no duplicate archive.
    r2 = cs.snapshot(snapshot_dir=tmp_path, skip_levels=False)
    assert r2.status == "unchanged"
    assert r2.levels_archive is None
    assert len(list((tmp_path / "archive").glob("*.csv.gz"))) == 1


def test_snapshot_archive_levels_flag_forces_a_copy(tmp_path, monkeypatch):
    def fake(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "index_levels" in url or "daily-index-levels" in url:
            _levels_csv(dest, ["2026-08-14"])
        else:
            _write(dest, _rows())

    monkeypatch.setattr(cs, "_download", fake)
    cs.snapshot(snapshot_dir=tmp_path, skip_levels=False)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=False, archive_levels=True)
    assert r.status == "unchanged"          # same quarter...
    assert r.levels_archive is None         # ...and that date is already archived


def test_levels_archive_failure_is_a_warning_not_a_failed_run(tmp_path, monkeypatch):
    """The constituent archive is the irreplaceable half; a levels-archive
    problem must not turn a successful capture into a failed run."""
    def fake(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "index_levels" in url or "daily-index-levels" in url:
            dest.write_text("Date,Name,Ticker,Level\n", encoding="utf-8")   # no dates
        else:
            _write(dest, _rows())

    monkeypatch.setattr(cs, "_download", fake)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=False)
    assert r.status == "ok"
    assert any("levels archive failed" in w for w in r.warnings)


def test_download_retries_transient_network_errors(tmp_path, monkeypatch):
    """The scheduled run fires on wake, often before DNS is up. One shot a week
    means a first-attempt URLError must not end the run."""
    import urllib.error

    calls = {"n": 0}

    def flaky(url, dest):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("temporary failure in name resolution")
        dest.write_text("ok", encoding="utf-8")

    import time

    monkeypatch.setattr(cs, "_download_once", flaky)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # backoff would add 15s
    cs._download("https://example.invalid/x.csv", tmp_path / "x.csv", attempts=4)
    assert calls["n"] == 3
    assert (tmp_path / "x.csv").read_text() == "ok"


def test_download_does_not_retry_a_404(tmp_path, monkeypatch):
    """A 4xx is a real answer — the URL moved. Retrying burns the run and
    hides the fact that the CRSP->Morningstar migration broke the path."""
    import urllib.error

    calls = {"n": 0}

    def gone(url, dest):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(cs, "_download_once", gone)
    with pytest.raises(urllib.error.HTTPError):
        cs._download("https://example.invalid/x.csv", tmp_path / "x.csv", attempts=4)
    assert calls["n"] == 1


# ── failure classification ───────────────────────────────────────────────────
#
# A moved URL and a network blip need opposite responses from the reader, so the
# job has to say which one happened rather than leaving it to the traceback.


@pytest.mark.parametrize("exc,want", [
    (cs.SourceMoved("landing page"), cs.MOVED),
    (cs.TransientDownload("4 attempts failed"), cs.TRANSIENT),
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), cs.MOVED),
    (urllib.error.HTTPError("u", 503, "Unavailable", {}, None), cs.TRANSIENT),
    (urllib.error.URLError("name resolution"), cs.TRANSIENT),
    (TimeoutError("timed out"), cs.TRANSIENT),
    (RuntimeError("something else entirely"), cs.UNKNOWN),
])
def test_classify_download_failure(exc, want):
    assert cs.classify_download_failure(exc) == want


def test_failure_guidance_is_ascii_and_actionable():
    """These strings are printed to a cp1252 console by the scheduled task. A
    non-ASCII character in the error path would raise UnicodeEncodeError at the
    exact moment the job is trying to report why it failed."""
    for kind in (cs.MOVED, cs.TRANSIENT, cs.CONTENT, cs.UNKNOWN):
        text = cs.failure_guidance(kind)
        text.encode("ascii")            # raises if a stray arrow/dash creeps in
        assert text.strip()
    assert "NOT help" in cs.failure_guidance(cs.MOVED)
    assert "Re-run" in cs.failure_guidance(cs.TRANSIENT)


@pytest.mark.parametrize("head,ctype,want", [
    (b"<!doctype html><html>", "text/html; charset=UTF-8", True),
    (b"\n  <html lang=en>", "", True),
    (b"TradeDate,Index Ticker", "text/html", True),        # header alone is enough
    (b"TradeDate,Index Ticker,Index Name", "text/csv", False),
    (b"Date,Name,Ticker,Level", "", False),
])
def test_looks_like_html(head, ctype, want):
    assert cs._looks_like_html(head, ctype) is want


def test_download_rejects_a_200_html_landing_page(tmp_path, monkeypatch):
    """The characteristic failure of a retired CDN path is not a 404 — it is the
    site's landing page served with HTTP 200, which parses as a one-column CSV.
    It must be caught as a MOVED url, not as a mysterious schema change."""
    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html; charset=UTF-8"}

        def geturl(self):
            return "https://indexes.morningstar.com/morningstar-market-indexes"

        def read(self, n=-1):
            return b"<!doctype html><html><body>Moved</body></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(cs.SourceMoved):
        cs._download_once("https://www.crsp.org/old/path.csv", tmp_path / "x.csv")
    assert not list(tmp_path.iterdir())     # no partial file left behind


def test_download_does_not_retry_a_moved_url(tmp_path, monkeypatch):
    """A moved URL is an answer. Re-asking a question already answered only
    delays the report of the answer."""
    calls = {"n": 0}

    def moved(url, dest):
        calls["n"] += 1
        raise cs.SourceMoved("landing page")

    monkeypatch.setattr(cs, "_download_once", moved)
    with pytest.raises(cs.SourceMoved):
        cs._download("https://example.invalid/x.csv", tmp_path / "x.csv", attempts=4)
    assert calls["n"] == 1


def test_snapshot_reports_moved_url_distinctly(tmp_path, monkeypatch):
    def moved(url, dest):
        raise cs.SourceMoved("HTTP 200 with an HTML page")

    monkeypatch.setattr(cs, "_download", moved)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "failed"
    assert r.failure_kind == cs.MOVED
    assert "URL MOVED" in r.errors[0]
    assert "indexes.morningstar.com" in r.errors[0]


def test_snapshot_reports_transient_network_distinctly(tmp_path, monkeypatch):
    """Same exit code, opposite response — so the message must differ. A
    transient failure must NOT tell the operator to go hunt for a new URL."""
    def flaky(url, dest):
        raise cs.TransientDownload("4 attempts failed; last error: URLError")

    monkeypatch.setattr(cs, "_download", flaky)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.status == "failed"
    assert r.failure_kind == cs.TRANSIENT
    assert "TRANSIENT NETWORK" in r.errors[0]
    assert "URL MOVED" not in r.errors[0]


def test_snapshot_verification_failure_is_content_not_moved(tmp_path, stub_download):
    """A file that arrives but is the wrong shape is a third fact, and pinning it
    to 'moved' would send the reader hunting a URL that is working fine."""
    stub_download["payload"] = _rows(n_total=5)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=True)
    assert r.failure_kind == cs.CONTENT
    assert "SOURCE CHANGED" in r.errors[0]


def test_levels_download_failure_is_classified_in_the_warning(tmp_path, monkeypatch):
    """The levels half is non-fatal, but a permanently moved levels URL is a
    standing task and a blip is not — so the warning has to say which."""
    payload = _rows()

    def fake(url, dest):
        if "daily-index-levels" in url:
            raise cs.SourceMoved("landing page")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write(dest, payload)

    monkeypatch.setattr(cs, "_download", fake)
    r = cs.snapshot(snapshot_dir=tmp_path, skip_levels=False)
    assert r.status == "ok"                 # constituents still captured
    assert any(f"levels download failed [{cs.MOVED}]" in w for w in r.warnings)


def test_write_report_records_the_failure_kind(tmp_path):
    r = cs.SnapshotResult(status="failed", failure_kind=cs.MOVED, errors=["boom"])
    text = cs.write_report(r, None, reports_dir=tmp_path, today="2026-07-28").read_text(
        encoding="utf-8"
    )
    assert "Failure kind" in text and "URL MOVED" in text


def test_write_report_flags_no_prior_snapshot(tmp_path):
    r = cs.SnapshotResult(status="ok", trade_date="2026-03-31", constituent_count=3477)
    p = cs.write_report(r, None, reports_dir=tmp_path, today="2026-07-28")
    text = p.read_text(encoding="utf-8")
    assert "No prior snapshot" in text
    assert "3,477" in text
