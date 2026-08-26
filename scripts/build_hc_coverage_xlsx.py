"""Build JP's Healthcare Services + MedTech coverage workbook.

One CURRENT file lives at `AA_Core Coverage.xlsx`; the previous
current file is moved into `archive/` stamped with the date it was built, so the
folder never accumulates dated copies at the top level. A stable filename is the
point -- it can be bookmarked, and the Google Sheet mirror keeps one identity.

Reads `exports/universe.csv` (the published artifact, never CM's internals) and
prices every row at Yahoo. Emits the workbook plus a flat CSV of the same rows
for the Google Sheet mirror.

    python scripts/build_hc_coverage_xlsx.py [--out-dir DIR] [--no-archive]

SYMBOLS GO THROUGH `ticker_utils.normalize_ticker`, NOT A PRIVATE ALIAS TABLE.
Coverage Manager's `Ticker` is correct by its own convention and is not rewritten
to suit a vendor -- `SHL GY` is how that row is keyed, and OpenFIGI confirms
ROG.SW is the Roche Genussschein. Yahoo needs a different string for many of
them, and `normalize_ticker` is where that translation already lives: the
space-suffix rule (`SHL GY` -> `SHL.DE`), `MANUAL_TICKER_MAP` for hyphenated
Nordic share classes (`COLOB DC` -> `COLO-B.CO`), and the Exchange fallback for
bare foreign symbols.

That last one is load-bearing rather than cosmetic. `MED` and `MOVE` are bare
SIX symbols that COLLIDE with live US listings, so a raw lookup returns Medifast
and Corvex -- a wrong company, not a missing one, which is the failure mode that
cost three repair passes in July and August 2026. `normalize_ticker` reads their
`Exchange` and yields `MED.SW` / `MOVE.SW`. Pass it the whole row, never just the
ticker string; it needs `Company Name` and `Exchange` to do either job.
"""
import argparse, csv, datetime, json, os, shutil, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE = os.path.join(REPO, "exports", "universe.csv")
DEFAULT_OUT = r"C:\Users\jroyp\Dropbox\Career\Pitches\Coverage"
STEM = "AA_Core Coverage"
# The stem before 2026-08-26. `archive_existing` only looks for the CURRENT stem,
# so without this the old workbook would sit at the top level next to the new one,
# still looking current. One-time migration; safe to leave in place forever.
PRIOR_STEMS = ("Coverage - HC Services and MedTech",)
LEGACY = os.path.join(DEFAULT_OUT, "Jason Peterson Coverage.xlsx")

# NOT renamed alongside the workbook. The Google Sheet mirror is a single
# =IMPORTDATA() cell pointed at this exact URL and nothing here can rewrite that
# cell, so renaming the endpoint silently empties the Sheet.
PUBLIC_CSV = os.path.join(REPO, "docs", "hc_coverage.csv")

RATINGS_DIR = r"C:\Users\jroyp\Dropbox\Companies_Stocks_Sectors_Ratings"
RATINGS_PATH = os.path.join(RATINGS_DIR, "Ratings_CoreCoverage.xlsx")
# Scope: rows flagged `Core` in the universe -- the names JP covers analytically
# (310 of 1,346, spanning every sector, not just the HC segment). JP 2026-08-26:
# "I just want stocks in there that are coded as part of core=Y in the coverage
# manager." Seeding the whole universe made a 1,346-row sheet nobody would fill in.

SECTORS = ("Healthcare Services", "MedTech")

# Calendar-year total returns, read from Coverage Manager's weekly performance
# snapshot rather than recomputed. JP, 2026-08-26: "You can just use a footnote to
# note when the returns are as of instead of re-running all the returns just for
# this report."
RETURN_COLS = ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "YTD"]
SNAPSHOT_MAX_AGE_DAYS = 10

# LC/SMID boundary. NOT derived from JP's reference sheet -- that sheet only bounds
# it to the open interval (USD 22.7bn SMID, USD 34.2bn LC], with no observation in
# between, so this is a named policy value inside that interval and not a measured
# one. Names near the line will flip with price and FX; the method is stated in the
# workbook so a flip reads as the rule working rather than as an error.
LC_THRESHOLD_USD_M = 25000

FIELDS = ["marketCap", "regularMarketPrice", "currentPrice", "currency",
          "forwardPE", "longName"]

COLS = (["Ticker", "Company Name", "Sector", "Subsector", "Sub-subsector",
         "Core Coverage", "Rating", "Listing", "Exchange", "Country (HQ)",
         "Ccy", "Price (local)", "Mkt Cap (USD $M)", "Size", "Fwd P/E"]
        + RETURN_COLS + ["Ramp Effort (1/2)"])

# ⛑ THE PUBLIC CSV IS A DIFFERENT SCHEMA, AND THAT IS THE POINT.
# `docs/hc_coverage.csv` is served by GitHub Pages to anyone with the URL. JP's
# ratings are a private judgement, unlike the position book already in this repo,
# so they are dropped on the way out. Adding a column to COLS therefore publishes
# it by default -- add anything sensitive to PRIVATE_ONLY at the same time.
PRIVATE_ONLY = {"Rating"}
PUBLIC_COLS = [c for c in COLS if c not in PRIVATE_ONLY]

WIDTH = {"Ticker": 11, "Company Name": 36, "Sector": 19, "Subsector": 26,
         "Sub-subsector": 20, "Core Coverage": 9, "Rating": 9, "Listing": 17,
         "Exchange": 16, "Country (HQ)": 15, "Ccy": 6, "Price (local)": 12,
         "Mkt Cap (USD $M)": 15, "Size": 7, "Fwd P/E": 9,
         "Ramp Effort (1/2)": 12}
WIDTH.update({c: 9 for c in RETURN_COLS})

HDR_FILL = PatternFill("solid", fgColor="1F3864")
RAMP_FILL = PatternFill("solid", fgColor="2E6B4F")
RAMP_CELL = PatternFill("solid", fgColor="E2EFDA")
HDR_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=13, color="1F3864")
SUB_FONT = Font(size=9, italic=True, color="595959")
THIN = Side(style="thin", color="BFBFBF")
SUMHDR = ["Subsector", "Companies", "Core", "Ramp 1", "Total Mkt Cap (USD $M)"]


def num(x):
    try:
        f = float(x)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def basesym(t):
    """'DAE SW' -> DAE, '1SXP.DE' -> 1SXP. Used ONLY to match JP's legacy sheet,
    never to key a vendor lookup -- a bare symbol is what causes the collisions."""
    return str(t or "").strip().upper().split(" ")[0].split(".")[0]


def size_bucket(mcap_usd_m):
    """LC / SMID / blank. Blank when the market cap is unknown -- the partial-book
    guard tolerates up to 5% missing, so a bucket must never be invented."""
    if mcap_usd_m is None:
        return None
    return "LC" if mcap_usd_m >= LC_THRESHOLD_USD_M else "SMID"


def forward_pe(raw):
    """Yahoo's forwardPE, or None. Non-positive is None, not a negative multiple.

    Yahoo returns a negative forwardPE for a company expected to lose money
    (Guardant: -380.9). That is the sign of the EPS estimate leaking through a
    ratio, not a valuation, and sorting a column containing it puts the biggest
    loss-maker at the top as if it were the cheapest name on the sheet.
    """
    v = num(raw)
    return v if (v is not None and v > 0) else None


def _payload_names_match(a, b):
    """Do two company names describe the same issuer?

    Reuses `universe.enrich._payload_names_match`, which is token-based precisely
    because character similarity cannot do this job -- Medartis/Medifast scores
    0.62 on difflib and they are different companies. Accents are stripped first:
    JP types these names by hand, so 'bioMerieux' must match 'bioMérieux'.
    Returns True when the comparison cannot be made (one side blank) -- an absent
    name is not evidence of a mismatch.
    """
    import unicodedata

    def strip_accents(s):
        return "".join(c for c in unicodedata.normalize("NFKD", str(s or ""))
                       if not unicodedata.combining(c))

    sys.path.insert(0, REPO)
    from universe.enrich import _payload_names_match as _match
    verdict = _match(strip_accents(a), strip_accents(b))
    return True if verdict is None else bool(verdict)


def fetch(rows):
    """rows: the universe dicts. Keyed by CM Ticker, valued by the Yahoo answer."""
    import yfinance as yf
    sys.path.insert(0, REPO)
    from ticker_utils import normalize_ticker

    symbols = {}
    for r in rows:
        sym = normalize_ticker(r["Ticker"], r.get("Company Name", ""), r.get("Exchange", ""))
        symbols[r["Ticker"]] = sym or r["Ticker"]

    def grab(cm):
        sym = symbols[cm]
        d = {}
        # Yahoo rate-limits aggressively once a session has pulled a few hundred
        # symbols, and a rate-limited response looks exactly like a company with
        # no market cap. Back off rather than accept the blank.
        for attempt in range(4):
            try:
                # ONE payload per attempt, then project. This was written as
                # `{k: yf.Ticker(sym).info.get(k) for k in FIELDS}`, which
                # re-fetches `.info` once PER FIELD -- 7 fields x 4 attempts x 239
                # tickers is up to 6,692 requests where 239 would do. That is what
                # throttled Yahoo hard enough to refuse 143 of 239 rows and send
                # this build to the FMP fallback three times in a row.
                info = yf.Ticker(sym).info or {}
                d = {k: info.get(k) for k in FIELDS}
                if d.get("marketCap"):
                    break
            except Exception as e:
                d = {"_error": str(e)[:90]}
            if attempt < 3:
                time.sleep(2 ** attempt)
        if not d.get("marketCap"):
            try:
                mc = getattr(yf.Ticker(sym).fast_info, "market_cap", None)
                if mc:
                    d["marketCap"] = float(mc)
            except Exception:
                pass
        d["_yf_symbol"] = sym
        return cm, d

    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for cm, d in ex.map(grab, list(symbols)):
            out[cm] = d

    # FMP for whatever Yahoo would not answer. Yahoo throttles a session that has
    # pulled a few hundred symbols and then stays throttled for a long while, which
    # is a bad single point of failure for a file that is supposed to stay current;
    # FMP is a 300/min tier and answered every row Yahoo refused when this was added.
    #
    # It is given the SAME normalized symbol. Note what this deliberately does NOT
    # do: `provider_chain`'s AlphaVantage fallback strips the suffix
    # (`ticker.split(".")[0]`), which turns MED.SW back into MED and hands you
    # Medifast -- re-introducing the wrong-company bug on a correctly-keyed row.
    # That is why this calls the FMP provider directly rather than the chain.
    missing = [cm for cm, d in out.items() if not d.get("marketCap")]
    if not missing:
        return out
    try:
        from config import API_KEYS
        from providers.fmp_provider import fetch_fundamentals as fmp_fetch
        key = API_KEYS.get("FMP_API_KEY", "")
    except Exception as e:
        print("  FMP fallback unavailable: %s" % str(e)[:70], file=sys.stderr)
        return out
    if not key:
        print("  FMP fallback skipped: no FMP_API_KEY", file=sys.stderr)
        return out

    print("  Yahoo gave no market cap for %d rows; trying FMP" % len(missing))

    def fmp_grab(cm):
        try:
            res, _is_ttm, ccy = fmp_fetch(symbols[cm], key, use_cache=True)
            return cm, res, ccy
        except Exception:
            return cm, None, None

    filled = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cm, res, ccy in ex.map(fmp_grab, missing):
            if not res or res.get("Mkt Cap") is None:
                continue
            d = out[cm]
            d["marketCap"] = res["Mkt Cap"]
            if res.get("Price") is not None:
                d["regularMarketPrice"] = res["Price"]
            if ccy:
                d["currency"] = ccy
            d["_source"] = "fmp"
            filled += 1
    print("  FMP filled %d of %d" % (filled, len(missing)))
    return out


def fetch_fx(currencies):
    """Rates via Coverage Manager's own fx_provider, which caches for 12h.

    Rolling a private FX fetcher here cost a build: 239 ticker lookups exhausted
    the Yahoo budget and the FX calls that followed were all throttled, so the
    run aborted with every price already in hand. CM's provider caches, so a
    same-day rebuild pays nothing. CALL THIS BEFORE THE TICKER SWEEP.

    GBp is the one currency it cannot answer -- there is no `GBpUSD=X` -- and it
    needs two different rates anyway. Yahoo quotes LSE PRICES in pence but
    reports those companies' MARKET CAP in whole pounds, so pence is right for
    one column and 100x wrong for the other. Market cap uses fx['GBP']; price
    uses fx['GBp'], derived here.
    """
    sys.path.insert(0, REPO)
    from providers.fx_provider import fetch_fx_rates

    wanted = {c for c in currencies if c and c != "USD"}
    ask = {("GBP" if c == "GBp" else c) for c in wanted}
    fx = dict(fetch_fx_rates(sorted(ask)))
    if "GBp" in wanted and fx.get("GBP"):
        fx["GBp"] = fx["GBP"] / 100.0
    for c in wanted:
        if c not in fx:
            fx[c] = None
            print("  FX FAILED for %s" % c, file=sys.stderr)
    return fx


def legacy_bases():
    """Tickers on JP's existing coverage sheet -> Ramp Effort 1."""
    if not os.path.exists(LEGACY):
        print("  legacy sheet not found; Ramp Effort left blank", file=sys.stderr)
        return set()
    ws = openpyxl.load_workbook(LEGACY, data_only=True)["JP Coverage"]
    out = set()
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=6, values_only=True):
        t = row[3]
        if t and str(t).strip().lower() not in ("ticker", "count", "#"):
            out.add(basesym(t))
    return out


def load_returns():
    """Calendar-year returns from the newest weekly performance snapshot.

    Returns (by_ticker, as_of_date). These are TOTAL returns off split- and
    dividend-adjusted prices, which may not match how JP's reference sheet
    computes its columns -- said on the sheet rather than assumed away.

    The snapshot's date is its BUILD date, not each security's last trading day;
    the per-series observation date is discarded before the pickle is written.
    So the footnote says "snapshot built <date>", which is the only claim the
    data actually supports.
    """
    import glob
    import pandas as pd

    paths = sorted(glob.glob(os.path.join(REPO, "cache", "perf", "perf_df_*.pkl")),
                   key=os.path.getmtime)
    if not paths:
        print("  no performance snapshot found; return columns left blank", file=sys.stderr)
        return {}, None
    path = paths[-1]
    as_of = datetime.date.fromtimestamp(os.path.getmtime(path))
    age = (datetime.date.today() - as_of).days
    if age > SNAPSHOT_MAX_AGE_DAYS:
        # Blank beats stale-and-unlabelled: a YTD from a month ago sitting beside a
        # live price reads as one consistent moment and is not one.
        print("  performance snapshot is %d days old (>%d); return columns left blank"
              % (age, SNAPSHOT_MAX_AGE_DAYS), file=sys.stderr)
        return {}, None
    df = pd.read_pickle(path).set_index("Ticker")
    df = df[~df.index.duplicated(keep="first")]
    out = {}
    for t, row in df.iterrows():
        out[str(t)] = {c: num(row.get(c)) for c in RETURN_COLS if c in df.columns}
    return out, as_of


def load_ratings():
    """JP's ratings, keyed by ticker. The build NEVER writes this file."""
    if not os.path.exists(RATINGS_PATH):
        print("  no ratings file at %s; Rating left blank" % RATINGS_PATH, file=sys.stderr)
        return {}
    try:
        ws = openpyxl.load_workbook(RATINGS_PATH, data_only=True)["Ratings"]
    except Exception as e:
        print("  ratings file unreadable (%s); Rating left blank" % str(e)[:70], file=sys.stderr)
        return {}
    hdr = [(c.value if c.value is None else str(c.value).strip())
           for c in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {h: i for i, h in enumerate(hdr) if h}
    if "Ticker" not in idx:
        print("  ratings file has no Ticker column; Rating left blank", file=sys.stderr)
        return {}
    out, dupes = {}, []
    for row in ws.iter_rows(min_row=2, values_only=True):
        t = row[idx["Ticker"]]
        if not t:
            continue
        t = str(t).strip()
        if t in out:
            dupes.append(t)
            continue
        out[t] = {
            "Rating": row[idx["Rating"]] if "Rating" in idx and idx["Rating"] < len(row) else None,
            "Company Name": (row[idx["Company Name"]]
                             if "Company Name" in idx and idx["Company Name"] < len(row) else None),
        }
    if dupes:
        # A duplicated key fans a left-join out into extra rows. Refuse rather than
        # silently pick one; JP has to resolve which rating is his.
        raise SystemExit("ABORT: duplicate Ticker in %s: %s. Nothing written."
                         % (RATINGS_PATH, ", ".join(sorted(set(dupes))[:10])))
    return out


def build_records(asof):
    rows = [r for r in csv.DictReader(open(UNIVERSE, encoding="utf-8"))
            if r["Sector (JP)"] in SECTORS]
    print("in scope: %d rows" % len(rows))
    # FX FIRST. The ticker sweep is what exhausts the Yahoo budget, and a run
    # that has every price but no rate to convert it with is a wasted run.
    fx = fetch_fx([r["Currency"] for r in rows] + ["GBP"])
    yf_data = fetch(rows)
    # A handful of rows quote in a currency the universe CSV does not claim
    # (SHMZF was a US OTC line recorded as JPY). Top those up rather than
    # dropping the row -- the vendor's currency is the one its numbers are in.
    extra = {(d.get("currency") or "").strip() for d in yf_data.values()}
    extra = {c for c in extra if c and c not in fx}
    if extra:
        print("  currencies Yahoo used that the universe did not declare: %s"
              % ", ".join(sorted(extra)))
        fx.update({k: v for k, v in fetch_fx(sorted(extra)).items() if k not in fx})
    legacy = legacy_bases()
    # Owens & Minor renamed to Accendra Health; same company, so it keeps the credit.
    renamed = {"ACH": "OMI"}
    returns, returns_asof = load_returns()
    ratings = load_ratings()

    # ⛑ Ticker is the join key on JP's instruction ("Ticker is an identity but it
    # can be fuzzy so you need to check and verify with me if its too ambiguous").
    # So: join on ticker, but where the ratings file's Company Name disagrees with
    # the universe's, DO NOT attach the rating -- collect it and make JP adjudicate.
    # A warning alone is not enough; the value has to be unreachable, or the
    # renderer publishes one issuer's rating against another's row.
    ambiguous = []

    recs = []
    for r in rows:
        t = r["Ticker"]
        d = yf_data.get(t, {})
        ccy = (d.get("currency") or r["Currency"] or "USD").strip()
        rate = fx.get("GBP") if ccy == "GBp" else fx.get(ccy)
        mc = num(d.get("marketCap"))
        px = num(d.get("regularMarketPrice")) or num(d.get("currentPrice"))
        known = basesym(t) in legacy or renamed.get(t.strip().upper()) in legacy
        mcap_usd_m = (mc * rate / 1e6) if (mc and rate) else None

        rating = None
        rr = ratings.get(t)
        if rr is not None:
            rated_name = str(rr.get("Company Name") or "").strip()
            if rated_name and not _payload_names_match(rated_name, r["Company Name"]):
                ambiguous.append((t, r["Company Name"], rated_name))
            else:
                rating = rr.get("Rating")

        rec = {
            "Ticker": t,
            "Company Name": r["Company Name"],
            "Sector": r["Sector (JP)"],
            "Subsector": r["Subsector (JP)"],
            "Sub-subsector": r["Sub-subsector (JP)"],
            "Core Coverage": "Y" if r.get("Core", "").strip().upper() == "Y" else "",
            "Rating": rating,
            "Listing": r["Listing Type"],
            "Exchange": r["Exchange"],
            "Country (HQ)": r["Country (HQ)"],
            "Ccy": ccy,
            "Price (local)": px,
            "Mkt Cap (USD $M)": mcap_usd_m,
            # Blank, never a guessed bucket, when the market cap is unknown. The
            # partial-book guard tolerates up to 5% missing, so this does happen.
            "Size": size_bucket(mcap_usd_m),
            # Yahoo's forwardPE only -- genuinely NTM. Never backfilled from FMP's
            # trailing P/E, and never from Price/FY1-estimate either: an annual FY1
            # figure is not NTM, and its currency and share basis are unvalidated
            # against the price (pence-vs-pounds alone is a 100x trap). A blank
            # here means "not known", which is a true statement.
            "Fwd P/E": forward_pe(d.get("forwardPE")),
            "Ramp Effort (1/2)": 1 if known else None,
        }
        rec.update({c: None for c in RETURN_COLS})
        rec.update(returns.get(t, {}))
        recs.append(rec)
    recs.sort(key=lambda r: (0 if r["Sector"] == "MedTech" else 1,
                             -(r["Mkt Cap (USD $M)"] or 0)))
    # REFUSE A PARTIAL BOOK. A rate-limited Yahoo response is indistinguishable
    # from a company that has no market cap, so a build that quietly drops a
    # third of the rows publishes a coverage list whose totals are simply wrong
    # -- and it overwrites the good file that was there. The first run of this
    # script hit exactly that: 105 of 239 rows blank, USD 3,089bn against a true
    # ~4,950bn, written without complaint beyond a warning line.
    dead_fx = [c for c, v in fx.items() if v is None]
    missing = [r["Ticker"] for r in recs if not r["Mkt Cap (USD $M)"]]
    if dead_fx:
        raise SystemExit("ABORT: no FX rate for %s. Nothing written; re-run when "
                         "the rate limit clears." % ", ".join(dead_fx))
    if len(missing) > max(5, 0.05 * len(recs)):
        raise SystemExit(
            "ABORT: %d of %d rows have no market cap (%s...). That is almost "
            "certainly rate limiting, not %d delisted companies. Nothing "
            "written; re-run when the rate limit clears."
            % (len(missing), len(recs), ", ".join(missing[:8]), len(missing)))
    if missing:
        print("  %d rows have no market cap: %s"
              % (len(missing), ", ".join(missing)), file=sys.stderr)

    # Cardinality gates. A join that matched NOTHING publishes an entirely blank
    # column and looks exactly like a column of honest blanks, which is how a
    # silently-broken join survives. Say it out loud instead.
    joined_returns = sum(1 for r in recs if any(r.get(c) is not None for c in RETURN_COLS))
    joined_ratings = sum(1 for r in recs if r.get("Rating") not in (None, ""))
    if returns and joined_returns == 0:
        raise SystemExit("ABORT: the performance snapshot matched 0 of %d rows. "
                         "Nothing written." % len(recs))
    print("  returns joined for %d/%d rows | ratings joined for %d"
          % (joined_returns, len(recs), joined_ratings))
    if ambiguous:
        print("  %d ticker(s) NOT rated - the ratings file names a different company:"
              % len(ambiguous), file=sys.stderr)
        for t, uni, rated in ambiguous:
            print("     %-10s universe=%-32s ratings=%s" % (t, uni[:32], rated),
                  file=sys.stderr)
    return recs, returns_asof, ambiguous


def write_sheet(wb, title, rows, subtitle):
    ws = wb.create_sheet(title)
    ws["A1"] = ("Healthcare Services & MedTech - Coverage List"
                if title == "Coverage List" else title)
    ws["A1"].font = TITLE_FONT
    ws["A2"] = subtitle
    ws["A2"].font = SUB_FONT
    hr = 4
    c = ws.cell(hr, 1, "#")
    c.font, c.fill = HDR_FONT, HDR_FILL
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for j, col in enumerate(COLS, start=2):
        c = ws.cell(hr, j, col)
        c.font = HDR_FONT
        c.fill = RAMP_FILL if col == "Ramp Effort (1/2)" else HDR_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[hr].height = 30

    for i, r in enumerate(rows, start=1):
        rr = hr + i
        ws.cell(rr, 1, i).alignment = Alignment(horizontal="center")
        for j, col in enumerate(COLS, start=2):
            cell = ws.cell(rr, j, r[col])
            if col == "Mkt Cap (USD $M)":
                cell.number_format = "#,##0"
            elif col == "Price (local)":
                cell.number_format = "#,##0.00"
            if col in ("Core Coverage", "Rating", "Ccy", "Ramp Effort (1/2)"):
                cell.alignment = Alignment(horizontal="center")
            if col == "Company Name":
                cell.font = Font(bold=True, size=10)
            if col == "Ramp Effort (1/2)" and r[col] == 1:
                cell.fill = RAMP_CELL
                cell.font = Font(bold=True, size=10, color="1F5132")
        for j in range(1, len(COLS) + 2):
            ws.cell(rr, j).border = Border(bottom=THIN)

    ws.freeze_panes = ws.cell(hr + 1, 4)
    ws.auto_filter.ref = "A%d:%s%d" % (hr, get_column_letter(len(COLS) + 1), hr + len(rows))
    ws.column_dimensions["A"].width = 5
    for j, col in enumerate(COLS, start=2):
        ws.column_dimensions[get_column_letter(j)].width = WIDTH[col]


def write_summary(wb, allr, mt, hs, asof, src):
    ws = wb.create_sheet("Summary")
    ws["A1"] = "Coverage Summary - Healthcare Services & MedTech"
    ws["A1"].font = TITLE_FONT
    nramp = sum(1 for r in allr if r["Ramp Effort (1/2)"] == 1)
    ws["A2"] = ("As of %s.  %d companies (%d MedTech, %d Healthcare Services).  "
                "%d marked Core Coverage.  %d marked Ramp Effort 1, %d blank."
                % (asof, len(allr), len(mt), len(hs),
                   sum(1 for r in allr if r["Core Coverage"] == "Y"), nramp, len(allr) - nramp))
    ws["A2"].font = SUB_FONT

    row = 4
    for sec, rows in (("MedTech", mt), ("Healthcare Services", hs)):
        ws.cell(row, 1, sec).font = Font(bold=True, size=11, color="1F3864")
        row += 1
        for j, h in enumerate(SUMHDR, start=1):
            c = ws.cell(row, j, h)
            c.font = HDR_FONT
            c.fill = RAMP_FILL if h == "Ramp 1" else HDR_FILL
            c.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.row_dimensions[row].height = 28
        row += 1
        subs = {}
        for r in rows:
            subs.setdefault(r["Subsector"] or "(unclassified)", []).append(r)
        for name, grp in sorted(subs.items(),
                                key=lambda kv: -sum(x["Mkt Cap (USD $M)"] or 0 for x in kv[1])):
            caps = [x["Mkt Cap (USD $M)"] for x in grp if x["Mkt Cap (USD $M)"]]
            ws.cell(row, 1, name)
            ws.cell(row, 2, len(grp)).alignment = Alignment(horizontal="center")
            ws.cell(row, 3, sum(1 for x in grp if x["Core Coverage"] == "Y")).alignment = Alignment(horizontal="center")
            c = ws.cell(row, 4, sum(1 for x in grp if x["Ramp Effort (1/2)"] == 1))
            c.alignment = Alignment(horizontal="center")
            c.fill = RAMP_CELL
            ws.cell(row, 5, sum(caps) if caps else None).number_format = "#,##0"
            row += 1
        tot = [x["Mkt Cap (USD $M)"] for x in rows if x["Mkt Cap (USD $M)"]]
        ws.cell(row, 1, sec + " total").font = Font(bold=True)
        for j, v in ((2, len(rows)),
                     (3, sum(1 for x in rows if x["Core Coverage"] == "Y")),
                     (4, sum(1 for x in rows if x["Ramp Effort (1/2)"] == 1))):
            c = ws.cell(row, j, v)
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center")
        c = ws.cell(row, 5, sum(tot))
        c.number_format, c.font = "#,##0", Font(bold=True)
        row += 3

    ws.cell(row, 1, "Notes").font = Font(bold=True, size=11, color="1F3864")
    for n in [
        src,
        "Rebuilt by Coverage Manager scripts/build_hc_coverage_xlsx.py. The previous version of this file is in archive/, stamped with the date it was built.",
        "Ramp Effort (1/2) = 1 where the name already appears in 'Jason Peterson Coverage.xlsx'. Matched on the base symbol, because the two files write some symbols differently (STMN.SW = 'STMN SW', 1SXP.DE = '1SXP', CVSG.L = 'CVSG', and so on).",
        "ACH (Accendra Health) counts as Ramp 1 because it is Owens & Minor renamed - 'OMI' on the legacy sheet.",
        "Core Coverage = the 'Core' flag on the Coverage Manager universe. It is a separate judgement from Ramp Effort.",
        "Prices are in local trading currency; market cap is USD at spot FX on the build date. The LSE names quote in pence (GBp).",
        "FOOTNOTE ON THE RETURN COLUMNS (2019-2025, YTD): these are TOTAL returns on split- and dividend-adjusted prices, and they are NOT as of the build date above - they come from the Coverage Manager weekly performance snapshot named in the caption. YTD in particular is as of that snapshot, while Price and Mkt Cap are same-day. A blank is a company that did not trade that year, never a zero.",
        "Size: LC at or above USD 25,000M market cap, SMID below. That threshold is a policy choice, not a measurement - the reference sheet only pins it between USD 22.7bn (SMID) and USD 34.2bn (LC). Names near the line move with price and FX.",
        "Fwd P/E is Yahoo's forwardPE (next twelve months) and is blank where Yahoo has none. It is deliberately never backfilled from a trailing P/E or from an annual FY1 estimate - both would be a different measure under the same heading.",
        "Rating is joined from Companies_Stocks_Sectors_Ratings/Ratings_CoreCoverage.xlsx (Core=Y names only), which this build never writes. It is omitted from the published CSV and the Google Sheet.",
        "Some rows are priced under a different symbol than the Ticker column shows - Coverage Manager keys them by its own convention and Yahoo needs another string. MED and MOVE are the ones that matter: their bare symbols collide with live US listings.",
    ]:
        row += 1
        c = ws.cell(row, 1, "- " + n)
        c.font = Font(size=9, color="404040")
        c.alignment = Alignment(vertical="top")
    for col, w in zip("ABCDE", (40, 12, 9, 10, 22)):
        ws.column_dimensions[col].width = w


def archive_existing(out_dir, current):
    """Move the current file into archive/, stamped with the date it was built.

    Also sweeps any PRIOR_STEMS file still sitting at the top level. Without that,
    the first run after a rename leaves the old workbook beside the new one,
    indistinguishable from a current file.
    """
    for stem in PRIOR_STEMS:
        old = os.path.join(out_dir, "%s.xlsx" % stem)
        if os.path.exists(old):
            arch = os.path.join(out_dir, "archive")
            os.makedirs(arch, exist_ok=True)
            built = datetime.date.fromtimestamp(os.path.getmtime(old)).isoformat()
            dest = os.path.join(arch, "%s - %s.xlsx" % (stem, built))
            n = 2
            while os.path.exists(dest):
                dest = os.path.join(arch, "%s - %s (%d).xlsx" % (stem, built, n))
                n += 1
            shutil.move(old, dest)
            print("migrated prior workbook -> archive/%s" % os.path.basename(dest))
        old_csv = os.path.join(out_dir, "%s.csv" % stem)
        if os.path.exists(old_csv):
            os.remove(old_csv)
    if not os.path.exists(current):
        return None
    arch_dir = os.path.join(out_dir, "archive")
    os.makedirs(arch_dir, exist_ok=True)
    built = datetime.date.fromtimestamp(os.path.getmtime(current)).isoformat()
    dest = os.path.join(arch_dir, "%s - %s.xlsx" % (STEM, built))
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(arch_dir, "%s - %s (%d).xlsx" % (STEM, built, n))
        n += 1
    shutil.move(current, dest)
    return dest


RATING_SHEET = "Ratings"
RATING_COLS = ["Ticker", "Company Name", "Sector (JP)", "Subsector (JP)",
               "Rating", "Notes", "Status", "Last Synced"]
HUMAN_COLS = {"Rating", "Notes"}          # never written by any code path
MACHINE_COLS = {"Sector (JP)", "Subsector (JP)", "Status", "Last Synced"}


def sync_ratings():
    """Seed / refresh the ratings workbook. Only reachable via --sync-ratings.

    The ordinary build NEVER calls this: the file is JP's to type in, and a daily
    automated job writing the file he is editing is how edits get lost.

    Ownership is split and enforced:
      * `Rating` and `Notes` are HUMAN. No code path writes them, including this one.
      * `Sector (JP)`, `Subsector (JP)`, `Status`, `Last Synced` are MACHINE and are
        refreshed, because otherwise they rot as the universe moves.
      * `Ticker` and `Company Name` are the identity, and are the interesting case.

    ⛑ A CHANGED `Company Name` IS NOT REFRESHED. That looks like a bug and is the
    whole safety mechanism. The build attaches a rating by ticker only when the
    ratings file's company name still agrees with the universe's; if this function
    quietly rewrote the name whenever it drifted, the two would agree by
    construction and the check could never fire again -- which is precisely how
    `ZEN` kept Zendesk's classification after the ticker became Zentek. Instead the
    row is marked `REVIEW - issuer may have changed`, the rating stops attaching,
    and JP adjudicates. Fail closed.
    """
    everything = list(csv.DictReader(open(UNIVERSE, encoding="utf-8")))
    uni = [r for r in everything if r.get("Core", "").strip().upper() == "Y"]
    # Keyed on the FULL universe, not just the core slice: a row that has dropped
    # out of Core still needs its name checked before its rating is trusted, and
    # "no longer core" is a different fact from "no longer exists".
    by_ticker = {r["Ticker"]: r for r in everything}
    core_tickers = {r["Ticker"] for r in uni}
    today = datetime.date.today().isoformat()
    print("core=Y in universe: %d of %d rows" % (len(uni), len(everything)))

    if os.path.exists(RATINGS_PATH):
        wb = openpyxl.load_workbook(RATINGS_PATH)
        if RATING_SHEET not in wb.sheetnames:
            raise SystemExit("ABORT: %s has no '%s' sheet." % (RATINGS_PATH, RATING_SHEET))
        ws = wb[RATING_SHEET]
        hdr = [(c.value if c.value is None else str(c.value).strip())
               for c in next(ws.iter_rows(min_row=1, max_row=1))]
        idx = {h: i + 1 for i, h in enumerate(hdr) if h}
        for need in ("Ticker", "Rating"):
            if need not in idx:
                raise SystemExit("ABORT: %s is missing the '%s' column." % (RATINGS_PATH, need))
    else:
        os.makedirs(os.path.dirname(RATINGS_PATH), exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = RATING_SHEET
        for j, c in enumerate(RATING_COLS, start=1):
            cell = ws.cell(1, j, c)
            cell.font = HDR_FONT
            cell.fill = RAMP_FILL if c in HUMAN_COLS else HDR_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 28
        idx = {c: j for j, c in enumerate(RATING_COLS, start=1)}

    seen, dupes, added, reviewed, retired, uncored = {}, [], 0, [], 0, 0
    for rr in range(2, ws.max_row + 1):
        t = ws.cell(rr, idx["Ticker"]).value
        if not t:
            continue
        t = str(t).strip()
        if t in seen:
            dupes.append(t)
            continue
        seen[t] = rr
    if dupes:
        raise SystemExit("ABORT: duplicate Ticker rows in %s: %s. Resolve by hand; "
                         "nothing written." % (RATINGS_PATH, ", ".join(sorted(set(dupes))[:10])))

    for t, rr in seen.items():
        u = by_ticker.get(t)
        if u is None:
            ws.cell(rr, idx["Status"], "Not in universe")
            ws.cell(rr, idx["Last Synced"], today)
            retired += 1
            continue
        if t not in core_tickers:
            # Dropped out of core coverage. The row and JP's rating STAY -- he formed
            # that view and it is not the sync's to discard -- but the status says so,
            # so a stale rating is visible rather than silently current.
            ws.cell(rr, idx["Status"], "No longer Core=Y")
            ws.cell(rr, idx["Last Synced"], today)
            uncored += 1
            continue
        stored_name = str(ws.cell(rr, idx["Company Name"]).value or "").strip()
        if stored_name and not _payload_names_match(stored_name, u["Company Name"]):
            # Leave EVERY cell alone except the flag. See the docstring.
            ws.cell(rr, idx["Status"], "REVIEW - issuer may have changed")
            reviewed.append((t, stored_name, u["Company Name"]))
            continue
        if not stored_name:
            ws.cell(rr, idx["Company Name"], u["Company Name"])
        ws.cell(rr, idx["Sector (JP)"], u["Sector (JP)"])
        ws.cell(rr, idx["Subsector (JP)"], u["Subsector (JP)"])
        ws.cell(rr, idx["Status"], "Active")
        ws.cell(rr, idx["Last Synced"], today)

    row = ws.max_row + 1
    for r in uni:
        if r["Ticker"] in seen:
            continue
        ws.cell(row, idx["Ticker"], r["Ticker"])
        ws.cell(row, idx["Company Name"], r["Company Name"])
        ws.cell(row, idx["Sector (JP)"], r["Sector (JP)"])
        ws.cell(row, idx["Subsector (JP)"], r["Subsector (JP)"])
        ws.cell(row, idx["Status"], "Active")
        ws.cell(row, idx["Last Synced"], today)
        row += 1
        added += 1

    ws.freeze_panes = "C2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(RATING_COLS)), ws.max_row - 1)
    for c, w in zip("ABCDEFGH", (12, 38, 22, 26, 10, 46, 30, 13)):
        ws.column_dimensions[c].width = w

    try:
        wb.save(RATINGS_PATH)
    except PermissionError:
        raise SystemExit("ABORT: %s is open in Excel. Close it and re-run; nothing written."
                         % RATINGS_PATH)

    print("ratings file: %s" % RATINGS_PATH)
    print("  rows now %d | added %d | not-in-universe %d | no-longer-core %d | "
          "flagged for review %d"
          % (ws.max_row - 1, added, retired, uncored, len(reviewed)))
    for t, old, new in reviewed:
        print("     REVIEW %-10s ratings says '%s', universe says '%s'" % (t, old, new))
    if reviewed:
        print("  Those rows keep their rating in this file but the coverage build will"
              " NOT attach it until the name is reconciled.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--sync-ratings", action="store_true",
                    help="Seed/refresh the ratings workbook, then exit. Never run by "
                         "the ordinary build.")
    args = ap.parse_args()

    if args.sync_ratings:
        sync_ratings()
        return

    asof = datetime.date.today().isoformat()
    recs, returns_asof, ambiguous = build_records(asof)
    mt = [r for r in recs if r["Sector"] == "MedTech"]
    hs = [r for r in recs if r["Sector"] == "Healthcare Services"]
    ret_note = ("Returns are from the Coverage Manager performance snapshot built %s"
                % returns_asof.isoformat()) if returns_asof else                "Returns unavailable (no fresh performance snapshot)"
    src = ("Source: Coverage Manager exports/universe.csv, filtered to Sector (JP) in "
           "(Healthcare Services, MedTech). Price and market cap pulled %s from Yahoo "
           "Finance, falling back to FMP per row where Yahoo had no answer. %s."
           % (asof, ret_note))

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    write_sheet(wb, "Coverage List", recs, "All %d names  |  %s" % (len(recs), src))
    write_sheet(wb, "MedTech", mt, "%d names  |  %s" % (len(mt), src))
    write_sheet(wb, "Healthcare Services", hs, "%d names  |  %s" % (len(hs), src))
    write_summary(wb, recs, mt, hs, asof, src)
    wb.move_sheet("Summary", offset=-4)

    # Save to a scratch path FIRST, so a failed write cannot leave the folder
    # with the old file already archived and no current file in its place.
    # A plain copy rather than os.replace: Dropbox holds a lock that makes
    # atomic replace fail with WinError 5.
    current = os.path.join(args.out_dir, "%s.xlsx" % STEM)
    tmp = os.path.join(tempfile.gettempdir(), "hc_coverage_%d.xlsx" % os.getpid())
    wb.save(tmp)
    if not args.no_archive:
        moved = archive_existing(args.out_dir, current)
        if moved:
            print("archived -> archive/%s" % os.path.basename(moved))
    try:
        shutil.copy2(tmp, current)
    except PermissionError:
        sys.exit("ERROR: %s is open in Excel. Close it and re-run. "
                 "(The previous version is in archive/ and the new one is at %s)"
                 % (current, tmp))
    finally:
        if os.path.exists(current):
            os.remove(tmp)
    print("wrote %s" % current)

    # Flat CSV, written twice on purpose:
    #   1. beside the workbook, so the folder is self-contained
    #   2. into docs/, which GitHub Pages serves at a STABLE public URL. The
    #      Google Sheet mirror is one =IMPORTDATA() cell pointed at that URL, so
    #      it refreshes itself and keeps ONE file id forever. The Drive connector
    #      can only rename and move a Sheet -- it cannot write cells -- so the
    #      alternative was replacing the file every build and minting a new URL
    #      each time, which breaks every link to it.
    def _flatten(cols):
        out = [["#"] + cols]
        for i, r in enumerate(recs, 1):
            out.append([i] + [
                ("" if r.get(c) is None else
                 (round(r[c], 2) if isinstance(r[c], float) else r[c])) for c in cols])
        return out

    # Beside the workbook: the FULL schema, ratings included. This folder is JP's.
    private_csv = os.path.join(args.out_dir, "%s.csv" % STEM)
    with open(private_csv, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(_flatten(COLS))
    print("wrote %s" % private_csv)

    # docs/: PUBLIC. Served by GitHub Pages to anyone with the URL, and read by the
    # Google Sheet. Ratings are dropped here -- see PRIVATE_ONLY.
    os.makedirs(os.path.dirname(PUBLIC_CSV), exist_ok=True)
    with open(PUBLIC_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(_flatten(PUBLIC_COLS))
    print("wrote %s  (public schema, %d of %d columns)"
          % (PUBLIC_CSV, len(PUBLIC_COLS), len(COLS)))
    print("  NOTE: docs/ is only served after a git commit+push of this repo.")

    print(json.dumps({
        "asof": asof, "rows": len(recs), "medtech": len(mt), "hc_services": len(hs),
        "ramp_1": sum(1 for r in recs if r["Ramp Effort (1/2)"] == 1),
        "total_mkt_cap_usd_bn": round(sum(r["Mkt Cap (USD $M)"] or 0 for r in recs) / 1000, 1),
    }, indent=1))


if __name__ == "__main__":
    main()
