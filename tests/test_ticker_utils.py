"""Tests for ticker_utils normalization and exchange mapping."""

from ticker_utils import (
    normalize_ticker, get_exchange_from_suffix, normalize_exchange,
    normalize_company_for_comparison, MANUAL_TICKER_MAP,
    EXCHANGE_NORMALIZE, COUNTRY_TO_ISO,
)


class TestNormalizeTicker:
    def test_manual_mapping_by_ticker(self):
        assert normalize_ticker("RDOR3") == "RDOR3.SA"
        assert normalize_ticker("BAYN") == "BAYN.DE"

    def test_manual_mapping_by_company(self):
        assert normalize_ticker("SomeTicker", "Olympus") == "7733.T"

    def test_space_separated_suffix(self):
        assert normalize_ticker("ROG SW") == "ROG.SW"
        assert normalize_ticker("GETIB SS") == "GETIB.ST"
        assert normalize_ticker("AZN LN") == "AZN.L"

    def test_dot_suffix_passthrough(self):
        assert normalize_ticker("4519.T") == "4519.T"
        assert normalize_ticker("BIOCON.NS") == "BIOCON.NS"

    def test_colon_format(self):
        assert normalize_ticker("LSE:AZN") == "LSE.AZN"

    def test_plain_us_ticker(self):
        assert normalize_ticker("AAPL") == "AAPL"
        assert normalize_ticker("MSFT") == "MSFT"

    def test_invalid_tickers(self):
        assert normalize_ticker("#N/A") is None
        assert normalize_ticker("") is None
        assert normalize_ticker("nan") is None

    def test_exchange_fallback_xetra(self):
        assert normalize_ticker("FRE", exchange="XETRA") == "FRE.DE"

    def test_exchange_fallback_six(self):
        assert normalize_ticker("SOON", exchange="SIX") == "SOON.SW"

    def test_exchange_fallback_lse(self):
        assert normalize_ticker("CVSG", exchange="LSE") == "CVSG.L"

    def test_exchange_fallback_tsx(self):
        assert normalize_ticker("CPH", exchange="TSX") == "CPH.TO"

    def test_exchange_fallback_nse(self):
        assert normalize_ticker("SUNPHARMA", exchange="NSE") == "SUNPHARMA.NS"

    def test_exchange_no_suffix_for_us(self):
        assert normalize_ticker("AAPL", exchange="NASDAQ") == "AAPL"
        assert normalize_ticker("JNJ", exchange="NYSE") == "JNJ"

    def test_exchange_manual_map_takes_precedence(self):
        # BAYN is in MANUAL_TICKER_MAP — should use that, not exchange fallback
        assert normalize_ticker("BAYN", exchange="XETRA") == "BAYN.DE"

    def test_exchange_ignored_when_dot_suffix_present(self):
        assert normalize_ticker("ROG.SW", exchange="SIX") == "ROG.SW"

    def test_exchange_ignored_when_space_suffix_present(self):
        assert normalize_ticker("AZN LN", exchange="LSE") == "AZN.L"

    def test_whitespace_handling(self):
        assert normalize_ticker("  AAPL  ") == "AAPL"


class TestGetExchangeFromSuffix:
    def test_dot_suffix(self):
        assert get_exchange_from_suffix("4519.T") == "TSE"
        assert get_exchange_from_suffix("BIOCON.NS") == "NSE"
        assert get_exchange_from_suffix("ROG.SW") == "SIX"

    def test_space_suffix(self):
        assert get_exchange_from_suffix("GETIB SS") == "OMX Stockholm"
        assert get_exchange_from_suffix("AZN LN") == "LSE"

    def test_no_suffix(self):
        assert get_exchange_from_suffix("AAPL") is None
        assert get_exchange_from_suffix("MSFT") is None

    def test_longest_dot_suffix_wins(self):
        # .SA should match B3, not hypothetical .S
        assert get_exchange_from_suffix("RDOR3.SA") == "B3"


class TestNormalizeExchange:
    def test_known_codes(self):
        assert normalize_exchange("NMS") == "NASDAQ"
        assert normalize_exchange("NGM") == "NASDAQ"
        assert normalize_exchange("NYQ") == "NYSE"
        assert normalize_exchange("ASE") == "NYSE American"
        assert normalize_exchange("PNK") == "OTC"
        assert normalize_exchange("BTS") == "BATS"
        assert normalize_exchange("PCX") == "NYSE Arca"

    def test_already_normalized(self):
        assert normalize_exchange("NASDAQ") == "NASDAQ"
        assert normalize_exchange("NYSE") == "NYSE"

    def test_case_insensitive(self):
        assert normalize_exchange("nms") == "NASDAQ"
        assert normalize_exchange("Nyq") == "NYSE"

    def test_unknown_passthrough(self):
        assert normalize_exchange("XETRA") == "XETRA"
        assert normalize_exchange("SomeExchange") == "SomeExchange"

    def test_empty_and_none(self):
        assert normalize_exchange("") == ""
        assert normalize_exchange(None) == ""

    def test_exact_matching_no_substring(self):
        # "NAS" should match NASDAQ exactly, but "XNAS" should not
        assert normalize_exchange("NAS") == "NASDAQ"
        # An unknown string that happens to contain "NAS" should NOT match
        assert normalize_exchange("XNAS") == "XNAS"


class TestNormalizeCompanyForComparison:
    def test_strips_suffixes(self):
        result = normalize_company_for_comparison("Apple Inc.")
        assert "inc" not in result
        assert "apple" in result

    def test_strips_corp(self):
        result = normalize_company_for_comparison("Microsoft Corporation")
        assert "corporation" not in result
        assert "microsoft" in result

    def test_empty_and_none(self):
        assert normalize_company_for_comparison("") == ""
        assert normalize_company_for_comparison(None) == ""


class TestCountryToIso:
    def test_common_countries(self):
        assert COUNTRY_TO_ISO["United States"] == "USA"
        assert COUNTRY_TO_ISO["Japan"] == "JPN"
        assert COUNTRY_TO_ISO["United Kingdom"] == "GBR"
        assert COUNTRY_TO_ISO["Germany"] == "DEU"
        assert COUNTRY_TO_ISO["China"] == "CHN"

    def test_all_exchange_countries_have_iso(self):
        from ticker_utils import EXCHANGE_TO_COUNTRY
        for exchange, country in EXCHANGE_TO_COUNTRY.items():
            assert country in COUNTRY_TO_ISO, f"Missing ISO mapping for {country} (exchange: {exchange})"


class TestIsinCheckDigit:
    """ISO 6166 mod-10 check digit — promoted from tests/test_foreign_crosscheck.py
    (2026-07-28). Arithmetic only: no vendor, no network, so it runs before any
    network check on the enrich write path."""

    def test_real_isins_pass(self):
        from ticker_utils import isin_check_digit_ok
        # Apple, Roche, AstraZeneca (London ordinary), Innovent (Cayman), Astellas
        for isin in ("US0378331005", "CH0012032048", "GB0009895292",
                     "KYG4818G1010", "JP3942400007"):
            assert isin_check_digit_ok(isin), isin

    def test_a_one_digit_typo_fails(self):
        from ticker_utils import isin_check_digit_ok
        assert not isin_check_digit_ok("US0378331004")   # Apple, last digit off by one
        assert not isin_check_digit_ok("US0378331015")   # Apple, one interior digit changed

    def test_the_live_csu_value_fails(self):
        """`CSU` carries `NET000CLBR01` — the value tonight's audit flagged as not
        structurally an ISIN. Its check digit is wrong too (the stem computes 9)."""
        from ticker_utils import isin_check_digit_ok
        assert not isin_check_digit_ok("NET000CLBR01")

    def test_case_and_embedded_whitespace_are_tolerated(self):
        """Hand-edited cells arrive with stray spaces and lower case; the VALUE is
        still the same ISIN, so normalize before judging."""
        from ticker_utils import isin_check_digit_ok
        assert isin_check_digit_ok("us0378331005")
        assert isin_check_digit_ok(" US0378331005 ")
        assert isin_check_digit_ok("US 0378 331 005")

    def test_malformed_nonblank_values_return_false(self):
        """Documented contract: anything that is not a structurally valid ISIN
        (2 letters + 9 alphanumerics + 1 digit) returns False — malformed is
        never 'unknown', because the caller treats False as 'do not store'."""
        from ticker_utils import isin_check_digit_ok
        assert not isin_check_digit_ok("US03783310")        # too short
        assert not isin_check_digit_ok("US03783310055")     # too long
        assert not isin_check_digit_ok("0S0378331005")      # digit where the country code goes
        assert not isin_check_digit_ok("US037833100A")      # letter where the check digit goes
        assert not isin_check_digit_ok("US03783-1005")      # non-alphanumeric
        assert not isin_check_digit_ok("error: not found")

    def test_empty_and_none_return_false(self):
        from ticker_utils import isin_check_digit_ok
        assert not isin_check_digit_ok("")
        assert not isin_check_digit_ok(None)
        assert not isin_check_digit_ok("   ")


class TestCountryIsinPrefixes:
    """R3: the prefix map must cover every country the universe actually
    contains, and countries with more than one legitimate prefix map to a SET."""

    def test_values_are_frozensets(self):
        from ticker_utils import COUNTRY_TO_ISIN_PREFIXES
        for country, prefixes in COUNTRY_TO_ISIN_PREFIXES.items():
            assert isinstance(prefixes, frozenset), country
            assert prefixes, f"{country} maps to an empty set"

    def test_previously_missing_countries_are_mapped(self):
        """The Codex R3 list: every one of these was silently unvalidatable."""
        from ticker_utils import COUNTRY_TO_ISIN_PREFIXES as M
        assert "IE" in M["Ireland"]
        assert "NL" in M["Netherlands"]
        assert "KY" in M["Cayman Islands"]
        assert "BM" in M["Bermuda"]
        assert "VG" in M["British Virgin Islands"]
        assert "JE" in M["Jersey"]
        assert "GG" in M["Guernsey"]
        assert "IM" in M["Isle of Man"]
        assert "PA" in M["Panama"]
        assert "IL" in M["Israel"]
        assert "SG" in M["Singapore"]
        assert "EE" in M["Estonia"]

    def test_channel_islands_and_iom_also_accept_gb(self):
        """The trap the map's shape exists for: Channel-Islands/IoM issuers
        commonly issue under GB as well as their own prefix. The reverse is NOT
        loosened — United Kingdom stays {GB} so the guard on UK rows keeps its
        teeth (a Guernsey-incorporated UK company is the Country (Incorporation)
        question, blocked pending JP's taxonomy decision)."""
        from ticker_utils import COUNTRY_TO_ISIN_PREFIXES as M
        assert M["Jersey"] == frozenset({"JE", "GB"})
        assert M["Guernsey"] == frozenset({"GG", "GB"})
        assert M["Isle of Man"] == frozenset({"IM", "GB"})
        assert M["United Kingdom"] == frozenset({"GB"})

    def test_every_live_universe_country_is_mapped_except_known_bad_values(self):
        """Coverage against the LIVE data — the gap this map had was invisible
        precisely because nothing measured it. `NL` (on MICC) is an alpha-2 code
        stored where a country name belongs: a data defect for JP to fix, not a
        mapping to add."""
        from ticker_utils import COUNTRY_TO_ISIN_PREFIXES, read_universe_csv
        df = read_universe_csv()
        known_bad = {"NL"}
        unmapped = set()
        for col in ("Country (HQ)", "Country (Listing)"):
            for v in df[col]:
                s = str(v).strip()
                if s and s not in COUNTRY_TO_ISIN_PREFIXES:
                    unmapped.add(s)
        assert unmapped <= known_bad, f"unmapped live countries: {sorted(unmapped)}"

    def test_iso2_map_agrees_with_iso3_map_on_shared_countries(self):
        """COUNTRY_TO_ISO2 (identity code, 1:1) and COUNTRY_TO_ISO (alpha-3)
        must describe the same countries the same way."""
        from ticker_utils import COUNTRY_TO_ISO, COUNTRY_TO_ISO2
        # Every alpha-3 country has an alpha-2, never the reverse constraint.
        for country in COUNTRY_TO_ISO:
            assert country in COUNTRY_TO_ISO2, country

    def test_every_prefix_set_contains_the_countrys_own_iso2(self):
        from ticker_utils import COUNTRY_TO_ISIN_PREFIXES, COUNTRY_TO_ISO2
        for country, iso2 in COUNTRY_TO_ISO2.items():
            assert iso2 in COUNTRY_TO_ISIN_PREFIXES[country], country
