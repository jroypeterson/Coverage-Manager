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
