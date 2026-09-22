"""CM writes sigma-alert's S&P 500 list (board #354, JP 2026-09-22).

`reporting.sigma_export.build_sp500_mirror` decides what to write into
`sigma-alert/sources/sp500.txt` + `sources/sp500_names.json` from CM's sp500 snapshot.
Every test here uses a tmp sigma-alert dir and an injected snapshot doc -- nothing reads
the real snapshot, writes the real sibling repo, or runs git.
"""

import csv
import json
from datetime import date

import pytest

from reporting import sigma_export as se

TODAY = date(2026, 9, 25)


def _tickers(n=503):
    return [f"T{i:03d}" for i in range(n)]


def _doc(tickers=None, as_of="2026-09-25", stale_days=45, names=None):
    tickers = _tickers() if tickers is None else tickers
    names = names or {}
    return {"as_of": as_of, "stale_days": stale_days,
            "holdings": [{"ticker": t, "name": names.get(t, f"{t} Corp")} for t in tickers]}


def _seed(target, tickers, updated="2026-09-18", names=None):
    (target / "sources").mkdir(parents=True, exist_ok=True)
    (target / "sources" / "sp500.txt").write_text(se.render_sp500_txt(tickers, updated),
                                                  encoding="utf-8")
    nm = names if names is not None else {t: f"{t} Corp" for t in tickers}
    (target / "sources" / "sp500_names.json").write_text(se.render_sp500_names(nm),
                                                         encoding="utf-8")


def _snapshot_bytes(target):
    return ((target / "sources" / "sp500.txt").read_bytes(),
            (target / "sources" / "sp500_names.json").read_bytes())


# --- the byte format is sigma-alert's refresh_sp500.py format, exactly -------------

def test_render_txt_matches_refresh_sp500_format():
    assert se.render_sp500_txt(["MMM", "BRK-B", "A", "A"], "2026-09-18") == (
        "# S&P 500 Constituents\n"
        "# Last updated: 2026-09-18\n"
        "# Check for reconstitution updates quarterly (March, June, September, December)\n"
        "# Source: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies\n"
        "A\nBRK-B\nMMM\n")


def test_render_names_matches_refresh_sp500_format():
    assert se.render_sp500_names({"ZTS": "Zoetis", "A": "Agilent"}) == (
        '{\n  "A": "Agilent",\n  "ZTS": "Zoetis"\n}\n')


# --- writes ------------------------------------------------------------------------

def test_newer_snapshot_with_a_changed_set_writes_both_files(tmp_path):
    old = _tickers()
    new = old[:-1] + ["NEWCO"]
    _seed(tmp_path, old)
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc(new))
    assert res["status"] == "changed"
    txt = res["files"][se.SP500_RELPATH]
    assert "# Last updated: 2026-09-25\n" in txt
    assert "NEWCO\n" in txt and f"\n{old[-1]}\n" not in txt
    assert "NEWCO" in json.loads(res["files"][se.SP500_NAMES_RELPATH])


def test_agreement_is_unchanged_and_never_churns_the_header(tmp_path):
    _seed(tmp_path, _tickers())
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc())
    assert res["status"] == "unchanged" and res["files"] == {}


def test_name_equal_to_ticker_is_kept(tmp_path):
    # IBM, MSCI, Uber: a real name that equals the ticker must not be dropped.
    tickers = _tickers(502) + ["IBM"]
    _seed(tmp_path, tickers)
    res = se.build_sp500_mirror(tmp_path, today=TODAY,
                                doc=_doc(tickers, names={"IBM": "IBM"}))
    assert json.loads(res["files"][se.SP500_NAMES_RELPATH])["IBM"] == "IBM"


def test_absent_files_are_written(tmp_path):
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc())
    assert set(res["files"]) == {se.SP500_RELPATH, se.SP500_NAMES_RELPATH}


# --- refusals: never overwrite a good list with a bad one --------------------------

@pytest.mark.parametrize("doc, why", [
    (None, "no S&P 500 snapshot"),
    ({"as_of": "2026-09-25", "holdings": []}, "no holdings"),
    (_doc(_tickers(494)), "implausible count 494"),
    (_doc(_tickers(511)), "implausible count 511"),
    (_doc(as_of="2026-08-10"), "46d old (limit 45d)"),
    (_doc(as_of="2026-09-26"), "in the future"),
    (_doc(as_of=None), "no usable as_of"),
    (_doc(_tickers(502) + ["T000"]), "T000 twice"),
    (_doc(_tickers(502) + [""]), "no ticker"),
])
def test_bad_snapshot_is_refused(tmp_path, monkeypatch, doc, why):
    monkeypatch.setattr(se, "_load_sp500_snapshot", lambda: doc)
    res = se.build_sp500_mirror(tmp_path, today=TODAY)
    assert res["status"] == "refused" and res["files"] == {}
    assert why in res["reason"]


def test_stale_limit_is_read_from_the_snapshot(tmp_path):
    # 46 days is fine when the snapshot says its own limit is 60.
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc(as_of="2026-08-10",
                                                                stale_days=60))
    assert res["status"] == "changed"


@pytest.mark.parametrize("as_of", ["2026-09-18", "2026-09-08"])
def test_same_day_or_older_disagreement_is_refused(tmp_path, as_of):
    # The live 2026-09-22 case: sigma-alert carried the September reconstitution,
    # CM's snapshot (observed the same day, and earlier) did not.
    current = _tickers()
    _seed(tmp_path, current, updated="2026-09-18")
    stale = current[:-3] + ["OLD1", "OLD2", "OLD3"]
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc(stale, as_of=as_of))
    assert res["status"] == "refused"
    assert "not newer" in res["reason"]


def test_older_snapshot_that_agrees_is_unchanged_not_refused(tmp_path):
    _seed(tmp_path, _tickers(), updated="2026-09-18")
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=_doc(as_of="2026-09-08"))
    assert res["status"] == "unchanged"


def test_loader_exception_is_a_refusal_not_a_crash(tmp_path, monkeypatch):
    def boom():
        raise OSError("dropbox lock")
    monkeypatch.setattr(se, "_load_sp500_snapshot", boom)
    assert se.build_sp500_mirror(tmp_path, today=TODAY)["status"] == "refused"


# --- export_and_push: same commit as ticker_metadata.json, and refusal leaves bytes -

@pytest.fixture
def export_env(monkeypatch, tmp_path):
    csv_path = tmp_path / "u.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Ticker", "Exchange", "Company Name",
                                          "Sector (JP)", "Subsector (JP)"])
        w.writeheader()
        w.writerow({"Ticker": "AAPL", "Exchange": "NASDAQ", "Company Name": "Apple Inc",
                    "Sector (JP)": "Tech", "Subsector (JP)": "Hardware"})
    from universe import positions as pos
    from universe import watchlist as wl
    pos_csv = tmp_path / "positions_and_researching.csv"
    monkeypatch.setattr(pos, "POSITIONS_PATH", pos_csv)
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pos_csv)
    pos.add("AAPL", position="Researching", path=pos_csv, universe_csv_path=csv_path,
            today="2026-04-11")
    target = tmp_path / "sigma-alert"
    (target / ".git").mkdir(parents=True)
    calls = []

    def fake_git(cwd, *args):
        calls.append(args)
        if args[:3] == ("diff", "--cached", "--quiet"):
            return "", 1
        return "", 0

    # Assert the negative: no real git may run from these tests.
    monkeypatch.setattr(se, "_git", fake_git)
    return csv_path, target, calls


def test_changed_list_rides_in_the_metadata_commit(export_env, monkeypatch):
    csv_path, target, calls = export_env
    _seed(target, _tickers(), updated="2026-09-18")
    new = _tickers()[:-1] + ["NEWCO"]
    monkeypatch.setattr(se, "_load_sp500_snapshot", lambda: _doc(new))
    res = se.export_and_push(csv_path, target_dir=target, push=False, today=TODAY)
    added = {c[1] for c in calls if c and c[0] == "add"}
    assert {"ticker_metadata.json", se.SP500_RELPATH, se.SP500_NAMES_RELPATH} <= added
    commit = [c for c in calls if c and c[0] == "commit"]
    assert len(commit) == 1 and "(+ S&P 500 list)" in commit[0][-1]
    assert res["sp500"]["status"] == "changed" and "files" not in res["sp500"]
    assert "NEWCO" in (target / se.SP500_RELPATH).read_text(encoding="utf-8")


def test_refusal_leaves_both_files_byte_identical(export_env, monkeypatch):
    csv_path, target, calls = export_env
    _seed(target, _tickers(), updated="2026-09-18")
    before = _snapshot_bytes(target)
    monkeypatch.setattr(se, "_load_sp500_snapshot", lambda: _doc(_tickers(400)))
    res = se.export_and_push(csv_path, target_dir=target, push=False, today=TODAY)
    assert _snapshot_bytes(target) == before
    assert res["sp500"]["status"] == "refused"
    added = {c[1] for c in calls if c and c[0] == "add"}
    assert se.SP500_RELPATH not in added and "ticker_metadata.json" in added


# --- the weekly status line makes a refusal audible --------------------------------

def test_weekly_status_refusal_is_failed():
    import weekly_universe as wu
    st = wu._sp500_mirror_status("pushed (5 tickers)",
                                 {"status": "refused", "reason": "implausible count 400"})
    assert st.startswith("failed:") and "implausible count 400" in st


def test_weekly_status_missing_summary_is_failed():
    import weekly_universe as wu
    assert wu._sp500_mirror_status("pushed (5 tickers)", None).startswith("failed:")


@pytest.mark.parametrize("state", ["changed", "unchanged"])
def test_weekly_status_success_is_not_failed(state):
    import weekly_universe as wu
    st = wu._sp500_mirror_status("pushed (5 tickers)",
                                 {"status": state, "count": 503, "as_of": "2026-09-25"})
    assert not st.startswith("failed") and f"sp500 {state}" in st


# --- the PUBLIC mirror carries tickers + names ONLY (IVV switch, Fable condition 4) ---

def test_mirror_payload_is_exactly_tickers_and_names(tmp_path):
    """sigma-alert is a public repo. An IVV-derived snapshot carries the fund's weight,
    market value, exchange and the iShares URL; none of it may cross the boundary."""
    tickers = _tickers()
    doc = _doc(tickers)
    doc["source"] = "https://www.ishares.com/us/products/239726/x/latest-holdings.csv"
    doc["kind"] = "ishares"
    doc["equity_weight_pct"] = 99.87
    for i, h in enumerate(doc["holdings"]):
        h.update({"weight_pct": 0.1234 + i, "market_value_usd": 7654321.5 + i,
                  "exchange": "NYSE", "location": "United States",
                  "market_currency": "USD", "sector": "Industrials",
                  "sub_industry": "Widgets", "name_source": "wikipedia"})
    _seed(tmp_path, tickers[:-1] + ["OLDCO"])
    res = se.build_sp500_mirror(tmp_path, today=TODAY, doc=doc)
    assert res["status"] == "changed"
    assert set(res["files"]) == {se.SP500_RELPATH, se.SP500_NAMES_RELPATH}

    txt = res["files"][se.SP500_RELPATH]
    body = [ln for ln in txt.splitlines() if not ln.startswith("#")]
    assert body == sorted(tickers)

    names = json.loads(res["files"][se.SP500_NAMES_RELPATH])
    assert names == {t: f"{t} Corp" for t in tickers}
    assert all(isinstance(v, str) for v in names.values())

    everything = (txt + res["files"][se.SP500_NAMES_RELPATH]).lower()
    for leak in ("ishares", "239726", "0.1234", "7654321", "99.87", "nyse",
                 "industrials", "widgets", "wikipedia\"", "weight", "market"):
        assert leak not in everything, leak
