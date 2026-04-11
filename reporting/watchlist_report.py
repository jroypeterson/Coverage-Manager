"""Weekly watchlist performance report (Monday).

Generates an HTML + Excel snapshot of the personal watchlist with:
  - current price (local currency, matches the buy/target currency)
  - % vs. buy price (positive = above buy, i.e. "expensive")
  - % vs. target price (negative = below target, i.e. room to run)
  - 1D / 1W / YTD / 1Y total return
  - notes column

Emailed and posted to Slack. Separate from the Friday coverage performance
report — the watchlist has a different audience (me, acting on positions) and
a different cadence (Monday).
"""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl

from config import API_KEYS, CSV_PATH, OLD_REPORTS_DIR, REPORTS_DIR, TODAY
from logging_utils import configure_logging, get_logger
from providers.yfinance_provider import batch_download_prices
from reporting.calcs import compute_returns
from reporting.email import archive_files, send_email_report
from reporting.slack import SLACK_CHANNEL, send_slack_notification
from ticker_utils import normalize_ticker
from universe import watchlist as wl

logger = get_logger("watchlist_report")

WATCHLIST_ARCHIVE_PATTERNS = [
    "watchlist_report_*.html",
    "watchlist_report_*.xlsx",
]

REPORT_HTML = REPORTS_DIR / f"watchlist_report_{TODAY}.html"
REPORT_XLSX = REPORTS_DIR / f"watchlist_report_{TODAY}.xlsx"


def _build_rows(entries, universe_df):
    """Join watchlist entries with universe metadata and normalized YF tickers."""
    idx = {str(r["Ticker"]).strip(): r for _, r in universe_df.iterrows()}
    rows = []
    for e in entries:
        t = e["Ticker"]
        u_row = idx.get(t)
        if u_row is None:
            logger.warning("%s not found in universe CSV — skipping", t)
            continue
        company = str(u_row.get("Company Name", "")).strip()
        exchange = str(u_row.get("Exchange", "")).strip()
        currency = str(u_row.get("Currency", "")).strip()
        yf_t = normalize_ticker(t, company, exchange)
        rows.append({
            "Ticker": t,
            "YF Ticker": yf_t,
            "Company": company,
            "Currency": currency,
            "Sector": str(u_row.get("Sector (JP)", "")).strip(),
            "Buy Price": e.get("Buy Price"),
            "Target Price": e.get("Target Price"),
            "Date Added": e.get("Date Added", ""),
            "Notes": e.get("Notes", ""),
        })
    return rows


def _latest_price(hist):
    if hist is None or len(hist) == 0:
        return None
    try:
        return float(hist.iloc[-1])
    except (TypeError, ValueError):
        return None


def _pct(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator - 1.0) * 100.0


def build_report_df():
    """Load the watchlist, fetch prices, and return a DataFrame ready to render."""
    entries = wl.load()
    if not entries:
        return pd.DataFrame(), 0

    errors, warnings = wl.validate(entries)
    for w in warnings:
        logger.info("WARN: %s", w)
    for err in errors:
        logger.warning("ERROR: %s", err)
    if errors:
        raise RuntimeError(
            f"Watchlist has {len(errors)} validation error(s); fix before running report"
        )

    universe_df = pd.read_csv(CSV_PATH)
    rows = _build_rows(entries, universe_df)

    yf_tickers = [r["YF Ticker"] for r in rows if r["YF Ticker"]]
    logger.info("Fetching price history for %d watchlist tickers...", len(yf_tickers))
    price_map = batch_download_prices(yf_tickers)

    for r in rows:
        hist = price_map.get(r["YF Ticker"])
        price = _latest_price(hist)
        r["Current Price"] = price
        returns = compute_returns(hist)
        r["1D %"] = returns.get("1D")
        r["1W %"] = returns.get("1W")
        r["YTD %"] = returns.get("YTD")
        r["1Y %"] = returns.get("1Y")
        r["% vs Buy"] = _pct(price, r.get("Buy Price"))
        r["% vs Target"] = _pct(price, r.get("Target Price"))

    cols = [
        "Ticker", "Company", "Sector", "Currency",
        "Current Price", "Buy Price", "Target Price",
        "% vs Buy", "% vs Target",
        "1D %", "1W %", "YTD %", "1Y %",
        "Date Added", "Notes",
    ]
    df = pd.DataFrame(rows)[cols]
    return df, len(entries)


# ── Rendering ───────────────────────────────────────────────────────────────

def _fmt_num(v, pct=False, prec=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    suffix = "%" if pct else ""
    return f"{v:,.{prec}f}{suffix}"


def write_html(df, path, today_str):
    pct_cols = {"% vs Buy", "% vs Target", "1D %", "1W %", "YTD %", "1Y %"}
    price_cols = {"Current Price", "Buy Price", "Target Price"}

    header_html = "".join(f"<th>{c}</th>" for c in df.columns)
    body_rows = []
    for _, row in df.iterrows():
        cells = []
        for c in df.columns:
            v = row[c]
            if c in pct_cols:
                txt = _fmt_num(v, pct=True)
                color = ""
                if isinstance(v, (int, float)) and not pd.isna(v):
                    if c == "% vs Target" and v < 0:
                        color = "color:#1a7f37;"  # below target = green opportunity
                    elif c == "% vs Buy" and v < 0:
                        color = "color:#1a7f37;"  # below buy = green (can add)
                    elif v > 0 and c in ("1D %", "1W %", "YTD %", "1Y %"):
                        color = "color:#1a7f37;"
                    elif v < 0 and c in ("1D %", "1W %", "YTD %", "1Y %"):
                        color = "color:#b91c1c;"
                cells.append(f"<td style='{color}text-align:right'>{txt}</td>")
            elif c in price_cols:
                cells.append(f"<td style='text-align:right'>{_fmt_num(v)}</td>")
            else:
                cells.append(f"<td>{'' if v is None or (isinstance(v, float) and pd.isna(v)) else v}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Watchlist — {today_str}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 24px; color: #111; }}
 h1 {{ font-size: 20px; margin: 0 0 6px; }}
 .sub {{ color: #666; font-size: 12px; margin-bottom: 16px; }}
 table {{ border-collapse: collapse; font-size: 12px; }}
 th, td {{ border: 1px solid #ddd; padding: 6px 10px; }}
 th {{ background: #f5f5f5; text-align: left; }}
 tr:nth-child(even) td {{ background: #fafafa; }}
</style></head><body>
<h1>Watchlist — {today_str}</h1>
<div class="sub">{len(df)} positions • prices in local currency</div>
<table>
 <thead><tr>{header_html}</tr></thead>
 <tbody>{''.join(body_rows)}</tbody>
</table>
</body></html>
"""
    path.write_text(html, encoding="utf-8")


def write_xlsx(df, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Watchlist"
    ws.append(list(df.columns))
    for _, row in df.iterrows():
        ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in row])
    wb.save(path)


def format_slack_summary(df, today_str):
    lines = [f"*Watchlist — {today_str}*", f"{len(df)} positions", ""]
    for _, r in df.iterrows():
        price = _fmt_num(r.get("Current Price"))
        buy = _fmt_num(r.get("Buy Price"))
        tgt = _fmt_num(r.get("Target Price"))
        vs_buy = _fmt_num(r.get("% vs Buy"), pct=True)
        vs_tgt = _fmt_num(r.get("% vs Target"), pct=True)
        lines.append(
            f"• *{r['Ticker']}* ({r.get('Currency','')}) {price} | buy {buy} ({vs_buy}) | tgt {tgt} ({vs_tgt})"
        )
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────

def main(skip_email=False, skip_slack=False, dry_run=False):
    configure_logging()
    os.makedirs(REPORTS_DIR, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Watchlist Weekly Report — %s", TODAY)
    logger.info("=" * 60)

    archive_files(REPORTS_DIR, OLD_REPORTS_DIR, TODAY, WATCHLIST_ARCHIVE_PATTERNS)

    df, entry_count = build_report_df()
    if entry_count == 0:
        logger.info("Watchlist is empty — nothing to report")
        return {"status": "empty", "entries": 0}

    logger.info("Writing HTML report to %s", REPORT_HTML)
    write_html(df, REPORT_HTML, TODAY)
    logger.info("Writing XLSX report to %s", REPORT_XLSX)
    write_xlsx(df, REPORT_XLSX)

    if dry_run:
        logger.info("Dry run — skipping email and Slack")
        return {"status": "ok_dry_run", "entries": entry_count, "html": str(REPORT_HTML)}

    # Email
    if skip_email:
        logger.info("Email: skipped")
    else:
        gmail_addr = API_KEYS.get("GMAIL_ADDRESS")
        gmail_pass = API_KEYS.get("GMAIL_APP_PASSWORD")
        if gmail_addr and gmail_pass:
            body = (
                f"Watchlist weekly report — {TODAY}\n"
                f"{entry_count} positions.\n"
                f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n"
            )
            send_email_report(
                gmail_addr, gmail_pass, [REPORT_HTML], TODAY,
                extra_attachments=[REPORT_XLSX], body_text=body,
            )
        else:
            logger.info("Email: skipped (GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set)")

    # Slack
    if skip_slack:
        logger.info("Slack: skipped")
    else:
        webhook = API_KEYS.get("SLACK_WEBHOOK_URL")
        if webhook:
            send_slack_notification(webhook, format_slack_summary(df, TODAY))
        else:
            logger.info("Slack: skipped (SLACK_WEBHOOK_URL not set)")

    logger.info("Watchlist report done (%d entries)", entry_count)
    return {"status": "ok", "entries": entry_count, "html": str(REPORT_HTML)}


if __name__ == "__main__":
    main()
