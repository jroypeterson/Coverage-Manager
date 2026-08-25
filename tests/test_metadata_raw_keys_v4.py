"""Schema v4: universe_metadata.json is keyed by the RAW ticker.

Before v4 the key was suffix-stripped, which did two silent kinds of damage:

  1. **It lost a company.** `ROG` (Rogers Corporation, Core=Y) and `ROG.SW`
     (Roche) both normalized to `ROG`; later-row-wins published Roche and
     Rogers Corporation had NO entry. The exporter reported
     `normalization_collisions: 1` on every run for months and it was read past.
  2. **It broke the obvious join for 183 of 1,096 rows.** `universe.csv` carries
     `Ticker = DIA.MI` while the metadata key was `DIA`, so
     `metadata[row["Ticker"]]` missed every suffixed row. `transcripts` iterates
     these keys AS tickers and `focus_today` keys its map by them while positions
     use the raw ticker.
"""
import json

import pytest

import config
from universe.artifacts import build_universe_metadata_with_stats


@pytest.fixture(scope="module")
def meta():
    m, s = build_universe_metadata_with_stats(config.CSV_PATH)
    return m, s


def test_a_bare_ticker_and_its_suffixed_sibling_both_survive(meta):
    """The specific damage the old key did: one of the two rows disappeared.

    This was pinned on `ROG` (Rogers Corporation, Core=Y) against `ROG.SW`
    (Roche) until 2026-08-25, when the `ROG` row was deleted. It had never been
    Rogers deliberately: the row was authored as Roche -- whose SIX ticker IS
    `ROG` -- and a bare-symbol lookup bound it to the US namesake, then enriched
    Rogers' CIK, FIGI, website and venue over the top. Its `Sub-subsector`
    survived as `NextGen Sequencing`, which is Roche's fingerprint, not Rogers'.

    NGEN.V / NGEN (NervGen Pharma: TSXV primary plus its NASDAQ cross-listing)
    is the same shape and carries the invariant now. Keep an example here that
    is a REAL pair of listings rather than a collision, so the test cannot be
    satisfied by the bug it exists to catch.
    """
    m, _ = meta
    assert m["NGEN.V"]["name"] == "NervGen Pharma Corp."
    assert m["NGEN"]["name"] == "NervGen Pharma Corp. Common stock"
    assert m["ROG.SW"]["name"] == "Roche"
    assert m["ROG.SW"]["core"] == "Y", "and it is a Core name, which is why it mattered"
    assert "ROG" not in m, "the bare ROG row was deleted on 2026-08-25; see the docstring"


def test_there_are_no_collisions_left(meta):
    _m, s = meta
    assert s["normalization_collisions"] == 0
    assert s["collision_examples"] == []


def test_the_map_is_one_to_one_with_the_csv(meta):
    """The invariant that makes `metadata[row["Ticker"]]` correct."""
    m, s = meta
    assert len(m) == s["rows_kept"] == s["rows_seen"]


def test_every_universe_ticker_resolves(meta):
    """The join every consumer actually wants. Under v3 this failed for 183 rows."""
    m, _ = meta
    from ticker_utils import read_universe_csv
    df = read_universe_csv()
    missing = [str(r["Ticker"]).strip() for _, r in df.iterrows()
               if str(r["Ticker"]).strip() and str(r["Ticker"]).strip() not in m]
    assert missing == [], f"{len(missing)} tickers do not resolve: {missing[:10]}"


def test_suffixed_tickers_keep_their_suffix(meta):
    m, _ = meta
    for t in ("DIA.MI", "ROG.SW", "GALD.SW", "BOI.PA", "2715.HK"):
        assert t in m, f"{t} lost its suffix"
    # And the bare base is NOT silently invented for them.
    assert "GALD" not in m


def test_the_published_status_declares_v4():
    import weekly_universe as wu
    assert wu.EXPORTS_SCHEMA_VERSION == 4
    status = json.loads((wu.EXPORTS_DIR / "universe_status.json").read_text(encoding="utf-8"))
    assert status["schema_version"] == 4
    assert status["normalization_collisions"] == 0
    assert status["ticker_count"] == status["row_count"], (
        "v4 is 1:1 — ticker_count + collisions == row_count, with collisions 0")


def test_the_stripper_is_gone_from_the_key_path():
    """Guards the DESIGN: `_normalize_ticker` is retained for the case-collision
    validator, but reintroducing it into the published key would re-create the
    exact defect this release fixed."""
    import inspect

    from universe import artifacts
    src = inspect.getsource(artifacts.build_universe_metadata_with_stats)
    assert "_publish_key" in src
    assert "_normalize_ticker" not in src
