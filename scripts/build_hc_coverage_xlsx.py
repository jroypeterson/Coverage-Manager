"""Build JP's Healthcare Services + MedTech coverage workbook.

One CURRENT file lives at `Coverage - HC Services and MedTech.xlsx`; the previous
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
STEM = "Coverage - HC Services and MedTech"
LEGACY = os.path.join(DEFAULT_OUT, "Jason Peterson Coverage.xlsx")

SECTORS = ("Healthcare Services", "MedTech")

FIELDS = ["marketCap", "regularMarketPrice", "currentPrice", "currency",
          "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "longName"]

COLS = ["Ticker", "Company Name", "Sector", "Subsector", "Sub-subsector",
        "Core Coverage", "Rating", "Listing", "Exchange", "Country (HQ)",
        "Ccy", "Price (local)", "Mkt Cap (USD $M)", "Ramp Effort (1/2)"]

WIDTH = {"Ticker": 11, "Company Name": 36, "Sector": 19, "Subsector": 26,
         "Sub-subsector": 20, "Core Coverage": 9, "Rating": 8, "Listing": 17,
         "Exchange": 16, "Country (HQ)": 15, "Ccy": 6, "Price (local)": 12,
         "Mkt Cap (USD $M)": 15, "Ramp Effort (1/2)": 12}

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
                d = {k: (yf.Ticker(sym).info or {}).get(k) for k in FIELDS}
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

    recs = []
    for r in rows:
        t = r["Ticker"]
        d = yf_data.get(t, {})
        ccy = (d.get("currency") or r["Currency"] or "USD").strip()
        rate = fx.get("GBP") if ccy == "GBp" else fx.get(ccy)
        mc = num(d.get("marketCap"))
        px = num(d.get("regularMarketPrice")) or num(d.get("currentPrice"))
        known = basesym(t) in legacy or renamed.get(t.strip().upper()) in legacy
        recs.append({
            "Ticker": t,
            "Company Name": r["Company Name"],
            "Sector": r["Sector (JP)"],
            "Subsector": r["Subsector (JP)"],
            "Sub-subsector": r["Sub-subsector (JP)"],
            "Core Coverage": "Y" if r.get("Core", "").strip().upper() == "Y" else "",
            "Rating": None,
            "Listing": r["Listing Type"],
            "Exchange": r["Exchange"],
            "Country (HQ)": r["Country (HQ)"],
            "Ccy": ccy,
            "Price (local)": px,
            "Mkt Cap (USD $M)": (mc * rate / 1e6) if (mc and rate) else None,
            "Ramp Effort (1/2)": 1 if known else None,
        })
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
    return recs


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
        "Some rows are priced under a different symbol than the Ticker column shows - Coverage Manager keys them by its own convention and Yahoo needs another string. MED and MOVE are the ones that matter: their bare symbols collide with live US listings.",
    ]:
        row += 1
        c = ws.cell(row, 1, "- " + n)
        c.font = Font(size=9, color="404040")
        c.alignment = Alignment(vertical="top")
    for col, w in zip("ABCDE", (40, 12, 9, 10, 22)):
        ws.column_dimensions[col].width = w


def archive_existing(out_dir, current):
    """Move the current file into archive/, stamped with the date it was built."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--no-archive", action="store_true")
    args = ap.parse_args()

    asof = datetime.date.today().isoformat()
    recs = build_records(asof)
    mt = [r for r in recs if r["Sector"] == "MedTech"]
    hs = [r for r in recs if r["Sector"] == "Healthcare Services"]
    src = ("Source: Coverage Manager exports/universe.csv, filtered to Sector (JP) in "
           "(Healthcare Services, MedTech). Prices and market caps from Yahoo Finance, "
           "pulled %s." % asof)

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
    rows_out = [["#"] + COLS]
    for i, r in enumerate(recs, 1):
        rows_out.append([i] + [
            ("" if r[c] is None else
             (round(r[c], 2) if isinstance(r[c], float) else r[c])) for c in COLS])
    for csv_path in (os.path.join(args.out_dir, "%s.csv" % STEM),
                     os.path.join(REPO, "docs", "hc_coverage.csv")):
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows_out)
        print("wrote %s" % csv_path)

    print(json.dumps({
        "asof": asof, "rows": len(recs), "medtech": len(mt), "hc_services": len(hs),
        "ramp_1": sum(1 for r in recs if r["Ramp Effort (1/2)"] == 1),
        "total_mkt_cap_usd_bn": round(sum(r["Mkt Cap (USD $M)"] or 0 for r in recs) / 1000, 1),
    }, indent=1))


if __name__ == "__main__":
    main()
