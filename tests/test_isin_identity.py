"""Tests for the ISIN -> issuer-name identity cross-check (`universe/isin_identity.py`).

Closes the same-country wrong-issuer hole pinned by
`test_guard_does_NOT_catch_a_same_country_wrong_issuer_isin` in
test_foreign_crosscheck.py: `validate_isin_for_row`'s prefix rule is a country
check, not an identity check — Nipro (`8086.T`) carried NMS Holdings'
`JP3750800009` and the prefix guard accepted it.

Every (stored name, OpenFIGI name) pair below is REAL: captured live from the
OpenFIGI v3 mapping API against current universe rows on 2026-07-28, not
invented. That includes the truncation forms — OpenFIGI truncates names at
~28 characters ("SUN PHARMACEUTICAL INDUS", "KINIKSA PHARMACEUTICALS INTE"),
which is the single biggest false-reject hazard for a name matcher.

No test here touches the network: fetches are injected fakes.
"""

import json

import pytest

from universe.isin_identity import (
    VERDICT_CONFLICT,
    VERDICT_INCONCLUSIVE,
    VERDICT_OK,
    check_isin_identity,
    fetch_isin_names,
    issuer_names_match,
    normalize_issuer_tokens,
    verify_isin_identity,
)


# ── name matching: real pairs that MUST match (false reject = guard gets ──────
# ── switched off) ─────────────────────────────────────────────────────────────

REAL_MATCHING_PAIRS = [
    # (universe "Company Name", OpenFIGI "name") — all captured live 2026-07-28
    ("Nipro Corporation", "NIPRO CORP"),
    ("Daiichi Sankyo Company, Limited", "DAIICHI SANKYO CO LTD"),
    ("OTSUKA HLDGS CO LTD", "OTSUKA HOLDINGS CO LTD"),   # HLDGS vs HOLDINGS
    ("Ono Pharmaceutical Co., Ltd.", "ONO PHARMACEUTICAL CO LTD"),
    ("Sysmex Corporation", "SYSMEX CORP"),
    ("Hoya Corporation", "HOYA CORP"),
    ("Yuhan Corporation", "YUHAN CORP"),
    ("Celltrion, Inc.", "CELLTRION INC"),
    ("ALTEOGEN Inc.", "ALTEOGEN INC"),
    ("PharmaEssentia Corporation", "PHARMAESSENTIA CORP"),
    ("Akeso, Inc.", "AKESO INC"),
    # OpenFIGI truncates at ~28 chars — every one of these is a real truncation
    ("CSPC Pharmaceutical Group Limited", "CSPC PHARMACEUTICAL GROUP LT"),
    ("CALIWAY BIOPHARMACEUTICALS CO L", "CALIWAY BIOPHARMACEUTICALS C"),
    ("Sun Pharmaceutical Industries Ltd", "SUN PHARMACEUTICAL INDUS"),
    ("Laboratorios Farmaceuticos Rovi, S.A.", "LABORATORIOS FARMACEUTICOS R"),
    ("Kiniksa Pharmaceuticals Internationl PLC",  # stored typo is real too
     "KINIKSA PHARMACEUTICALS INTE"),
    ("Oncodesign Precision Medicine Société anonyme",  # accents
     "ONCODESIGN PRECISION MEDICIN"),
    # share-class / line suffixes on the OpenFIGI side
    ("Beijing Tiantan Biological Products Co., Ltd.",
     "BEIJING TIANTAN BIOLOGICAL-A"),
    ("Swedish Orphan Biovitrum AB (publ)", "SWEDISH ORPHAN BIOVITRUM-BTA"),
    ("Berkshire Hathaway Inc. Class B", "BERKSHIRE HATHAWAY INC-CL B"),
    ("Alphabet Inc Class C", "ALPHABET INC"),
    # punctuation / ampersand / bare-name forms
    ("WuXi Biologics (Cayman) Inc.", "WUXI BIOLOGICS CAYMAN INC"),
    ("Eli Lilly and Company", "ELI LILLY & CO"),
    ("Fagron NV", "FAGRON"),
    ("Monday.Com Ltd", "MONDAY.COM LTD"),
    ("Nano-X Imaging Ltd.", "NANO-X IMAGING LTD"),
    ("Bausch + Lomb Corporation", "BAUSCH + LOMB CORP"),
    ("Novocure Limited", "NOVOCURE LTD"),
    ("Tiziana Life Sciences Ltd - ADR", "TIZIANA LIFE SCIENCES LTD"),
    ("Stevanato Group S.p.A.", "STEVANATO GROUP SPA"),
    ("Qiagen N.V.", "QIAGEN N.V."),
    ("AstraZeneca PLC", "ASTRAZENECA PLC"),
    ("Neuren Pharmaceuticals Limited", "NEUREN PHARMACEUTICALS LTD"),
    ("Crispr Therapeutics AG", "CRISPR THERAPEUTICS AG"),
    ("Biocon Limited", "BIOCON LTD"),
    ("Virbac SA", "VIRBAC SA"),
    ("Xenon Pharmaceuticals Inc", "XENON PHARMACEUTICALS INC"),
    ("Wave Life Sciences Ltd", "WAVE LIFE SCIENCES LTD"),
    ("Intuitive Surgical Inc", "INTUITIVE SURGICAL INC"),
    # first full-universe pass (2026-07-28) false conflicts, all real rows:
    # sponsored-ADR line names on the issuer's own US ISIN…
    ("Teva Pharmaceutical Industries Ltd", "TEVA PHARMACEUTICAL-SP ADR"),
    ("Taiwan Semiconductor Manufacturing Company Limited",
     "TAIWAN SEMICONDUCTOR-SP ADR"),
    # …and a vendor-mangled stored name with the vowels squeezed out. The
    # ISIN (US1598641074) genuinely is Charles River's; the NAME is the ugly
    # side, so a conflict verdict would be factually wrong.
    ("Charles River Lbrtrs ntrntl Inc", "CHARLES RIVER LABORATORIES"),
]


@pytest.mark.parametrize("stored,figi", REAL_MATCHING_PAIRS,
                         ids=[p[0][:24] for p in REAL_MATCHING_PAIRS])
def test_real_universe_names_match_their_openfigi_forms(stored, figi):
    assert issuer_names_match(stored, figi), f"{stored!r} vs {figi!r}"


# ── name matching: real pairs that MUST conflict (each is a live or ──────────
# ── corrected wrong-issuer row; the OpenFIGI name is the ISIN's real owner) ──

REAL_CONFLICT_PAIRS = [
    # THE bug this module exists for: same-country wrong issuer (8086.T)
    ("Nipro Corporation", "NMS HOLDINGS CO"),
    # the other corrected-2026-07-28 rows
    ("Yuhan Corporation", "YURANUS INFRASTRUCTURE LTD"),
    ("Akeso, Inc.", "KESORAM INDUSTRIES LTD"),
    ("Zentek Ltd", "ZEN TECHNOLOGIES LTD"),
    ("Hoya Corporation", "HOMAG GROUP AG"),
    ("Hoya Corporation", "ROYAL INDIA CORP LTD"),
    # live suspects found by the first audit sample, 2026-07-28
    ("Cipher Pharmaceuticals Inc", "CPH CHEMIE & PAPIER HLDG-REG"),
    ("Cipher Pharmaceuticals Inc", "CPH GROUP AG"),
    ("Genfit SA", "GIFT HOLDINGS INC"),
    ("Sonova Holding AG", "SOON LIAN HOLDINGS LTD"),
    ("Boiron SA", "WINGARA AG LTD"),
    ("Crocs Inc", "CROSSWOOD"),
    ("Gerresheimer AG", "GUIZHOU GUIHANG AUTOMOTIVE-A"),
    ("Icade SA", "ICANDY INTERACTIVE LTD"),
    ("Optima Health PLC", "OPT MACHINE VISION TECH CO-A"),
    ("Financiere de Tubize SA", "TUBACEX SA"),
    ("Medela Potentia Tbk PT", "MALARASEN AB"),
    ("Galderma Group AG", "GALADA FINANCE LTD"),
    ("Estun Automation Co Ltd", "ELKOP ESTONIA SE"),
    ("Evotec SE", "EKOTECHNIKA AG"),
    ("Sandoz Group AG", "SDM SE"),
]


@pytest.mark.parametrize("stored,figi", REAL_CONFLICT_PAIRS,
                         ids=[f"{p[0][:14]}--{p[1][:14]}" for p in REAL_CONFLICT_PAIRS])
def test_real_wrong_issuer_names_do_not_match(stored, figi):
    assert not issuer_names_match(stored, figi), f"{stored!r} vs {figi!r}"


def test_zen_prefix_shorter_than_four_chars_never_matches_zentek():
    """'zen' is a prefix of 'zentek' — the truncation tolerance must not turn
    a 3-letter stem into a match, or Zen Technologies matches Zentek again."""
    assert not issuer_names_match("Zentek Ltd", "ZEN TECHNOLOGIES LTD")
    assert not issuer_names_match("Zen Technologies Ltd", "ZENTEK LTD")


def test_gdr_line_of_same_issuer_matches_and_is_documented_limitation():
    """`US7169722037` is PharmaEssentia's GDR line; OpenFIGI names it
    'PHARMAESSENTIA CORP-GDS REGS'. The identity check is an ISSUER check:
    same issuer, wrong listing PASSES here by design — the listing-mismatch
    class belongs to crosscheck-foreign, not this module."""
    assert issuer_names_match("PharmaEssentia Corporation",
                              "PHARMAESSENTIA CORP-GDS REGS")


def test_normalize_strips_legal_forms_but_never_to_empty():
    assert normalize_issuer_tokens("Fagron NV") == ["fagron"]
    assert normalize_issuer_tokens("OTSUKA HLDGS CO LTD") == ["otsuka"]
    # a name that is ALL legal tokens must fall back rather than vanish
    assert normalize_issuer_tokens("Group Ltd") != []


# ── verdicts: three states, and inconclusive is never ok ─────────────────────


def test_check_conflict_when_no_openfigi_name_matches():
    res = check_isin_identity("Nipro Corporation", ["NMS HOLDINGS CO"])
    assert res.verdict == VERDICT_CONFLICT
    assert "NMS HOLDINGS CO" in res.openfigi_names


def test_check_ok_when_any_openfigi_name_matches():
    # CPH's ISIN maps to two names; a renamed issuer keeps its ISIN, so ANY
    # matching name is agreement.
    res = check_isin_identity("CPH Group AG",
                              ["CPH CHEMIE & PAPIER HLDG-REG", "CPH GROUP AG"])
    assert res.verdict == VERDICT_OK


def test_check_inconclusive_on_transport_failure_not_ok():
    """The whole point: an unreachable API must NOT read as validated."""
    res = check_isin_identity("Nipro Corporation", None)
    assert res.verdict == VERDICT_INCONCLUSIVE
    assert res.reason == "openfigi-unreachable"


def test_check_inconclusive_when_openfigi_has_no_coverage():
    # Real case: BAVA.CO's stored `SGXZ32918005` gets "No identifier found."
    res = check_isin_identity("Bavarian Nordic A/S", [])
    assert res.verdict == VERDICT_INCONCLUSIVE
    assert res.reason == "no-openfigi-coverage"


def test_check_inconclusive_without_a_company_name_to_compare():
    res = check_isin_identity("", ["NIPRO CORP"])
    assert res.verdict == VERDICT_INCONCLUSIVE
    assert res.reason == "no-company-name"


def test_verify_uses_injected_fetch_and_returns_conflict():
    def fake_fetch(isins, **kwargs):
        assert isins == ["JP3750800009"]
        return {"JP3750800009": ["NMS HOLDINGS CO"]}

    res = verify_isin_identity("JP3750800009", "Nipro Corporation",
                               fetch=fake_fetch)
    assert res.verdict == VERDICT_CONFLICT


# ── fetch: cache deterministic outcomes, never transient failures ────────────


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_caches_names_and_no_coverage_but_not_failures(tmp_path):
    cache_path = tmp_path / "openfigi_isin_names.json"
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append([j["idValue"] for j in json])
        payload = []
        for job in json:
            if job["idValue"] == "JP3673600007":
                payload.append({"data": [{"name": "NIPRO CORP"}]})
            elif job["idValue"] == "SGXZ32918005":
                payload.append({"warning": "No identifier found."})
            else:
                pytest.fail(f"unexpected isin {job['idValue']}")
        return _FakeResp(200, payload)

    out = fetch_isin_names(["JP3673600007", "SGXZ32918005"],
                           cache_path=cache_path, post=fake_post,
                           sleep=lambda s: None)
    assert out == {"JP3673600007": ["NIPRO CORP"], "SGXZ32918005": []}
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["JP3673600007"]["names"] == ["NIPRO CORP"]
    assert cached["SGXZ32918005"]["names"] == []  # deterministic no-result: cached

    # second call: served from cache, no network
    out2 = fetch_isin_names(["JP3673600007", "SGXZ32918005"],
                            cache_path=cache_path, post=fake_post,
                            sleep=lambda s: None)
    assert out2 == out
    assert len(calls) == 1


def test_fetch_transport_failure_returns_none_and_is_not_cached(tmp_path):
    cache_path = tmp_path / "openfigi_isin_names.json"

    def broken_post(url, json=None, timeout=None, headers=None):
        raise OSError("connection refused")

    out = fetch_isin_names(["JP3673600007"], cache_path=cache_path,
                           post=broken_post, sleep=lambda s: None)
    assert out == {"JP3673600007": None}
    assert not cache_path.exists() or "JP3673600007" not in json.loads(
        cache_path.read_text(encoding="utf-8"))


def test_fetch_http_error_returns_none_uncached(tmp_path):
    cache_path = tmp_path / "openfigi_isin_names.json"

    def post_500(url, json=None, timeout=None, headers=None):
        return _FakeResp(500)

    out = fetch_isin_names(["JP3673600007"], cache_path=cache_path,
                           post=post_500, sleep=lambda s: None)
    assert out == {"JP3673600007": None}


def test_fetch_retries_once_after_429(tmp_path):
    cache_path = tmp_path / "openfigi_isin_names.json"
    attempts = []

    def post_429_then_200(url, json=None, timeout=None, headers=None):
        attempts.append(1)
        if len(attempts) == 1:
            return _FakeResp(429)
        return _FakeResp(200, [{"data": [{"name": "NIPRO CORP"}]}])

    out = fetch_isin_names(["JP3673600007"], cache_path=cache_path,
                           post=post_429_then_200, sleep=lambda s: None)
    assert out == {"JP3673600007": ["NIPRO CORP"]}
    assert len(attempts) == 2


def test_no_cache_flag_refetches(tmp_path):
    cache_path = tmp_path / "openfigi_isin_names.json"
    cache_path.write_text(json.dumps(
        {"JP3673600007": {"names": ["STALE NAME"], "fetched_at": "2026-01-01"}}),
        encoding="utf-8")
    out = fetch_isin_names(
        ["JP3673600007"], cache_path=cache_path, use_cache=False,
        post=lambda url, json=None, timeout=None, headers=None:
            _FakeResp(200, [{"data": [{"name": "NIPRO CORP"}]}]),
        sleep=lambda s: None)
    assert out == {"JP3673600007": ["NIPRO CORP"]}


def test_audit_no_isin_count_is_unaffected_by_sample_truncation():
    """Regression: `no_isin` was computed after --sample cut the row list,
    so a 15-row sample reported 1,084 rows as ISIN-less."""
    import pandas as pd

    from universe.isin_identity import audit_universe

    df = pd.DataFrame([
        {"Ticker": "A", "Company Name": "Alpha Co", "ISIN": "US0000000001"},
        {"Ticker": "B", "Company Name": "Beta Co", "ISIN": "US0000000002"},
        {"Ticker": "C", "Company Name": "Gamma Co", "ISIN": ""},
    ])
    result = audit_universe(
        df, sample=1,
        fetch=lambda isins, **kw: {i: ["ALPHA CO"] for i in isins})
    assert result["checked"] == 1
    assert result["no_isin"] == 1   # only C — not "everything sample skipped"


# ── the write path: enrich must not write an unverified ISIN ─────────────────
# The companion to test_foreign_crosscheck's
# `test_guard_does_NOT_catch_a_same_country_wrong_issuer_isin`: the prefix
# layer still accepts NMS Holdings' ISIN for Nipro, but the identity layer
# now rejects it before it can land in a row.

from unittest.mock import patch  # noqa: E402

from universe.enrich import enrich_single_ticker  # noqa: E402
from universe.isin_identity import IsinIdentityResult  # noqa: E402


def _fmp_nipro_like(ticker):
    """FMP-style profile whose ISIN passes the prefix guard (JP on a JP row)
    but belongs to another issuer — the exact 8086.T failure shape."""
    return {
        "symbol": ticker,
        "companyName": "Nipro Corporation",
        "isin": "JP3750800009",   # NMS Holdings' — same country, wrong issuer
        "exchange": "NYSE",       # FMP-normalizable exchange for a clean row
        "currency": "JPY",
        "country": "Japan",
    }


def _run_enrich_with_identity(identity_result):
    with patch("universe.enrich._fetch_fmp_profile", side_effect=_fmp_nipro_like), \
         patch("universe.enrich.fetch_openfigi_identifiers", return_value={}), \
         patch("universe.enrich.fetch_sec_cik_map", return_value={}), \
         patch("universe.enrich.verify_isin_identity",
               return_value=identity_result) as mock_verify, \
         patch("universe.enrich.yf.Ticker") as mock_yf:
        mock_yf.return_value.isin = "-"
        mock_yf.return_value.info = {}
        row = enrich_single_ticker("8086.T", sector_jp="MedTech")
    return row, mock_verify


def test_enrich_single_ticker_drops_isin_on_identity_conflict():
    row, mock_verify = _run_enrich_with_identity(IsinIdentityResult(
        VERDICT_CONFLICT, "issuer-name-mismatch", ("NMS HOLDINGS CO",)))
    assert row["ISIN"] == ""            # the same-country hole, now closed
    assert row["Company Name"] == "Nipro Corporation"
    mock_verify.assert_called_once()


def test_enrich_single_ticker_drops_isin_when_identity_inconclusive():
    """Unreachable OpenFIGI must NOT read as validated: defer the write.
    A blank cell is refilled by the next enrich run; a wrong value looks
    like data forever."""
    row, _ = _run_enrich_with_identity(IsinIdentityResult(
        VERDICT_INCONCLUSIVE, "openfigi-unreachable"))
    assert row["ISIN"] == ""


def test_enrich_single_ticker_keeps_isin_on_identity_ok():
    row, _ = _run_enrich_with_identity(IsinIdentityResult(
        VERDICT_OK, "matched", ("NIPRO CORP",)))
    assert row["ISIN"] == "JP3750800009"


def test_bulk_enrich_checks_identity_only_for_blank_isin_cells():
    """The bulk pipeline fills blank cells only, so the identity API call is
    spent only where a write can happen — and a conflict blocks that write."""
    import pandas as pd

    from universe.enrich import fetch_yfinance_identifiers

    df = pd.DataFrame([
        # blank ISIN cell + wrong-issuer candidate from yfinance -> checked, blocked
        {"Ticker": "8086.T", "Company Name": "Nipro Corporation",
         "Exchange": "Tokyo", "ISIN": "",
         "Country (HQ)": "Japan", "Country (Listing)": "Japan"},
        # already-filled ISIN cell -> enrich_dataframe will never write it; no API spend
        {"Ticker": "4543.T", "Company Name": "Terumo Corporation",
         "Exchange": "Tokyo", "ISIN": "JP3546800008",
         "Country (HQ)": "Japan", "Country (Listing)": "Japan"},
    ])

    class FakeYF:
        isin = "JP3750800009"
        info = {}

    identity_calls = []

    def fake_verify(isin, company, **kwargs):
        identity_calls.append((isin, company))
        return IsinIdentityResult(VERDICT_CONFLICT, "issuer-name-mismatch",
                                  ("NMS HOLDINGS CO",))

    with patch("universe.enrich.yf.Ticker", return_value=FakeYF()), \
         patch("universe.enrich.verify_isin_identity", side_effect=fake_verify), \
         patch("universe.enrich.time.sleep", lambda s: None):
        results = fetch_yfinance_identifiers(df)

    assert identity_calls == [("JP3750800009", "Nipro Corporation")]
    assert "ISIN" not in results.get("8086.T", {})      # conflict: not written
    assert results.get("4543.T", {}).get("ISIN") == "JP3750800009"  # parity value; never written to a filled cell


def test_cache_write_failure_is_a_warning_not_an_exception(tmp_path, monkeypatch, caplog):
    """A cache is reconstructible; an approved company's row is not.

    Live 2026-08-06: Dropbox held openfigi_isin_names.json mid-sync, os.replace
    raised PermissionError, and it propagated out of verify_isin_identity ->
    enrich_single_ticker and aborted adding an approved candidate to the
    universe. _load_cache had always degraded gracefully; the write side had not.
    """
    import os as _os
    from universe import isin_identity as ii

    target = tmp_path / "c.json"

    def boom(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(ii.os, "replace", boom)
    with caplog.at_level("WARNING"):
        ii._save_cache(target, {"US0378331005": ["APPLE INC"]})   # must not raise

    assert any("cache not written" in r.message for r in caplog.records)
    assert not list(tmp_path.glob("*.tmp.json")), "orphan tmp file left behind"
