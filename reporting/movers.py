"""Weekly movers report — flag and explain extreme weekly performance.

Consumes the perf snapshot DataFrame produced by reporting/generate.py and
flags tickers whose 1-week return is extreme either in absolute terms or
relative to their Sector (JP) cohort. For each flagged ticker, fetches
recent news headlines from Finnhub and asks Claude Haiku for a 2-3 line
"why" summary.

Outputs:
- ``coverage_movers_<date>.html`` — table view with company, sector, move,
  z-score, why, and headlines drilldown.
- ``coverage_movers_<date>.md`` — same content in markdown for the email/
  archive path.
- A formatted plain-text Slack summary string (caller posts to webhook).
"""

from __future__ import annotations

import html
import math
from datetime import date

import pandas as pd

from logging_utils import get_logger

logger = get_logger("reporting.movers")


def compute_flags(
    df: pd.DataFrame,
    abs_threshold_pct: float,
    z_threshold: float,
    min_peer_count: int,
) -> pd.DataFrame:
    """Return a DataFrame of flagged movers, sorted by absolute weekly move.

    Adds three columns to flagged rows:
        - ``_z_score``  z-score vs Sector (JP) peers (NaN if cohort too small)
        - ``_abs_flag`` True if |1W| >= abs_threshold_pct
        - ``_z_flag``   True if |z| >= z_threshold

    A row is flagged if either ``_abs_flag`` or ``_z_flag`` is True. Rows
    missing 1W (None / NaN) are excluded.
    """
    if df.empty or "1W" not in df.columns:
        return df.iloc[0:0].copy()

    work = df.copy()
    # Coerce to float; missing returns become NaN and drop out of flagging.
    work["1W"] = pd.to_numeric(work["1W"], errors="coerce")
    work = work[work["1W"].notna()].copy()
    if work.empty:
        return work

    sectors = work.get("Sector (JP)", pd.Series([""] * len(work))).fillna("").astype(str).str.strip()
    work["_sector_key"] = sectors

    # Per-cohort mean and stdev. Only computed for cohorts with at least
    # min_peer_count members; everyone else gets NaN and skips the z-flag.
    grouped = work.groupby("_sector_key")["1W"]
    means = grouped.transform("mean")
    stds = grouped.transform("std")
    counts = grouped.transform("count")
    work["_z_score"] = (work["1W"] - means) / stds.where(stds > 0)
    work.loc[counts < min_peer_count, "_z_score"] = pd.NA

    work["_abs_flag"] = work["1W"].abs() >= abs_threshold_pct
    work["_z_flag"] = work["_z_score"].abs() >= z_threshold
    work["_z_flag"] = work["_z_flag"].fillna(False)

    flagged = work[work["_abs_flag"] | work["_z_flag"]].copy()
    if flagged.empty:
        return flagged

    flagged["_abs_move"] = flagged["1W"].abs()
    flagged = flagged.sort_values("_abs_move", ascending=False).drop(columns="_abs_move")
    return flagged


def cap_flagged(flagged: pd.DataFrame, max_flagged: int) -> pd.DataFrame:
    """Trim to the top-N flagged rows by |1W|. Pre-sorted by ``compute_flags``."""
    if len(flagged) <= max_flagged:
        return flagged
    logger.info("Capping flagged movers from %d to %d (top by |1W|)", len(flagged), max_flagged)
    return flagged.head(max_flagged).copy()


def enrich_with_news(
    flagged: pd.DataFrame,
    finnhub_key: str,
    days_back: int = 7,
    max_items: int = 8,
) -> pd.DataFrame:
    """Attach a ``_news`` list-of-dicts column to each flagged row.

    Empty list on any failure or missing key. Tolerates non-US tickers by
    just returning empty news for them — Finnhub's company-news endpoint is
    most reliable for US listings.
    """
    if not finnhub_key:
        flagged = flagged.copy()
        flagged["_news"] = [[] for _ in range(len(flagged))]
        return flagged

    from providers.finnhub_news import fetch_company_news

    enriched = flagged.copy()
    news_col = []
    for ticker in enriched["Ticker"].tolist():
        items = fetch_company_news(ticker, finnhub_key, days_back=days_back, max_items=max_items)
        news_col.append(items)
    enriched["_news"] = news_col
    return enriched


def enrich_with_summaries(
    flagged: pd.DataFrame,
    anthropic_key: str,
    model: str = "claude-haiku-4-5",
) -> pd.DataFrame:
    """Attach a ``_why`` summary string to each flagged row.

    Empty string when the Anthropic key is missing, headlines are empty, or
    the call fails. Caller renders the headline list as fallback.
    """
    enriched = flagged.copy()
    if not anthropic_key:
        enriched["_why"] = "" * len(enriched)
        enriched["_why"] = [""] * len(enriched)
        return enriched

    from providers.anthropic_summary import summarize_move

    summaries = []
    for _, row in enriched.iterrows():
        weekly = row.get("1W")
        try:
            weekly_pct = float(weekly) if weekly is not None and not (isinstance(weekly, float) and math.isnan(weekly)) else 0.0
        except (TypeError, ValueError):
            weekly_pct = 0.0
        summary = summarize_move(
            ticker=str(row.get("Ticker", "")),
            company=str(row.get("Company Name", "")),
            sector=str(row.get("Sector (JP)", "")),
            weekly_pct=weekly_pct,
            headlines=row.get("_news") or [],
            api_key=anthropic_key,
            model=model,
        )
        summaries.append(summary)
    enriched["_why"] = summaries
    return enriched


# ── Rendering ────────────────────────────────────────────────────────────────


def _fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    try:
        return f"{float(val):+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_z(val) -> str:
    if val is None or pd.isna(val):
        return "—"
    try:
        return f"{float(val):+.2f}"
    except (TypeError, ValueError):
        return "—"


def render_html(flagged: pd.DataFrame, today: str, abs_threshold: float, z_threshold: float) -> str:
    """Render the flagged DataFrame as a self-contained HTML report."""
    if flagged.empty:
        return _empty_html(today, abs_threshold, z_threshold)

    rows_html = []
    for _, r in flagged.iterrows():
        ticker = html.escape(str(r.get("Ticker", "")))
        company = html.escape(str(r.get("Company Name", "")))
        sector = html.escape(str(r.get("Sector (JP)", "")))
        subsector = html.escape(str(r.get("Subsector (JP)", "")))
        wk = _fmt_pct(r.get("1W"))
        zs = _fmt_z(r.get("_z_score"))
        why = html.escape(str(r.get("_why") or "").strip())
        flags = []
        if r.get("_abs_flag"):
            flags.append("|1W|≥" + f"{abs_threshold:.0f}%")
        if r.get("_z_flag"):
            flags.append(f"|z|≥{z_threshold:.1f}")
        flags_str = " · ".join(flags)

        # Headlines drilldown
        news = r.get("_news") or []
        news_html_parts = []
        for h in news[:5]:
            d = html.escape(h.get("date", ""))
            src = html.escape(h.get("source", ""))
            head = html.escape(h.get("headline", ""))
            url = html.escape(h.get("url", ""))
            if url:
                news_html_parts.append(f'<li><span class="muted">[{d} · {src}]</span> <a href="{url}">{head}</a></li>')
            else:
                news_html_parts.append(f'<li><span class="muted">[{d} · {src}]</span> {head}</li>')
        news_html = "<ul class='news'>" + "".join(news_html_parts) + "</ul>" if news_html_parts else "<span class='muted'>no headlines</span>"

        why_html = f"<p class='why'>{why}</p>" if why else ""

        direction_class = "up" if (r.get("1W") or 0) >= 0 else "down"
        rows_html.append(
            f"""
            <tr class='{direction_class}'>
              <td><strong>{ticker}</strong><br><span class='muted'>{company}</span></td>
              <td>{sector}{('<br><span class="muted">' + subsector + '</span>') if subsector else ''}</td>
              <td class='num'>{wk}</td>
              <td class='num'>{zs}</td>
              <td><span class='flags'>{flags_str}</span></td>
              <td>{why_html}{news_html}</td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Coverage Movers — {today}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; color: #222; margin: 24px; max-width: 1200px; }}
h1 {{ font-size: 20px; margin-bottom: 4px; }}
.subtitle {{ color: #666; margin-bottom: 20px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid #e0e0e0; padding: 8px 10px; vertical-align: top; text-align: left; }}
th {{ background: #f7f7f7; font-weight: 600; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.up td.num {{ color: #117a44; }}
tr.down td.num {{ color: #b00020; }}
.muted {{ color: #888; font-size: 12px; }}
.flags {{ font-size: 12px; color: #555; background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }}
.why {{ margin: 0 0 6px 0; font-size: 13px; line-height: 1.4; }}
ul.news {{ margin: 0; padding-left: 18px; font-size: 12px; color: #333; }}
ul.news li {{ margin: 2px 0; }}
ul.news a {{ color: #0a58ca; text-decoration: none; }}
ul.news a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Coverage Movers — {today}</h1>
<p class="subtitle">{len(flagged)} flagged ticker(s). Threshold: |1W| ≥ {abs_threshold:.1f}% OR sector-cohort |z| ≥ {z_threshold:.1f}.</p>
<table>
<thead><tr>
  <th>Ticker</th><th>Sector</th><th>1W</th><th>z (sector)</th><th>Trigger</th><th>Why / Headlines</th>
</tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</body>
</html>"""


def _empty_html(today: str, abs_threshold: float, z_threshold: float) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Coverage Movers — {today}</title></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px;">
<h1>Coverage Movers — {today}</h1>
<p>No tickers crossed the threshold this week (|1W| ≥ {abs_threshold:.1f}% OR sector |z| ≥ {z_threshold:.1f}).</p>
</body></html>"""


def render_markdown(flagged: pd.DataFrame, today: str, abs_threshold: float, z_threshold: float) -> str:
    """Render the flagged DataFrame as markdown."""
    if flagged.empty:
        return (
            f"# Coverage Movers — {today}\n\n"
            f"No tickers crossed the threshold this week "
            f"(|1W| ≥ {abs_threshold:.1f}% OR sector |z| ≥ {z_threshold:.1f}).\n"
        )

    lines = [
        f"# Coverage Movers — {today}",
        "",
        f"{len(flagged)} flagged ticker(s). Threshold: |1W| ≥ {abs_threshold:.1f}% OR sector-cohort |z| ≥ {z_threshold:.1f}.",
        "",
        "| Ticker | Company | Sector | 1W | z (sector) | Trigger | Why |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for _, r in flagged.iterrows():
        triggers = []
        if r.get("_abs_flag"):
            triggers.append(f"abs≥{abs_threshold:.0f}%")
        if r.get("_z_flag"):
            triggers.append(f"z≥{z_threshold:.1f}")
        why = (r.get("_why") or "").strip().replace("|", "\\|").replace("\n", " ")
        if not why:
            news = r.get("_news") or []
            if news:
                why = "(no LLM summary; headlines: " + "; ".join(h.get("headline", "")[:80] for h in news[:2]) + ")"
            else:
                why = "(no news available)"
        lines.append(
            f"| {r.get('Ticker','')} | {str(r.get('Company Name','')).replace('|','\\|')} | "
            f"{str(r.get('Sector (JP)','')).replace('|','\\|')} | "
            f"{_fmt_pct(r.get('1W'))} | {_fmt_z(r.get('_z_score'))} | "
            f"{' / '.join(triggers)} | {why} |"
        )
    return "\n".join(lines) + "\n"


def format_slack_summary(flagged: pd.DataFrame, today: str, abs_threshold: float, z_threshold: float) -> str:
    """Plain-text Slack summary. Top 10 movers, no markdown tables."""
    header = f"*Coverage Movers — {today}*"
    if flagged.empty:
        return (
            f"{header}\n\nNo tickers crossed the threshold "
            f"(|1W| ≥ {abs_threshold:.1f}% OR sector |z| ≥ {z_threshold:.1f}). Quiet week."
        )

    n_total = len(flagged)
    top = flagged.head(10)
    up = top[top["1W"] >= 0]
    down = top[top["1W"] < 0]

    lines = [header, "", f"{n_total} flagged · showing top {len(top)} by |1W|"]
    if not up.empty:
        lines.append("")
        lines.append("📈 *Up*")
        for _, r in up.iterrows():
            why = (r.get("_why") or "").strip()
            why = (why[:140] + "…") if len(why) > 140 else why
            lines.append(
                f"  • *{r['Ticker']}* {_fmt_pct(r.get('1W'))} (z {_fmt_z(r.get('_z_score'))}) — "
                f"{r.get('Company Name','')}"
                + (f"\n    _{why}_" if why else "")
            )
    if not down.empty:
        lines.append("")
        lines.append("📉 *Down*")
        for _, r in down.iterrows():
            why = (r.get("_why") or "").strip()
            why = (why[:140] + "…") if len(why) > 140 else why
            lines.append(
                f"  • *{r['Ticker']}* {_fmt_pct(r.get('1W'))} (z {_fmt_z(r.get('_z_score'))}) — "
                f"{r.get('Company Name','')}"
                + (f"\n    _{why}_" if why else "")
            )
    return "\n".join(lines)


# ── Top-level orchestration ────────────────────────────────────────────────


def run(
    perf_df: pd.DataFrame,
    today: str,
    finnhub_key: str = "",
    anthropic_key: str = "",
    abs_threshold_pct: float = 10.0,
    z_threshold: float = 2.0,
    min_peer_count: int = 5,
    max_flagged: int = 30,
    llm_model: str = "claude-haiku-4-5",
) -> dict:
    """Compute, enrich, and render. Returns dict with html, md, slack, count."""
    flagged = compute_flags(perf_df, abs_threshold_pct, z_threshold, min_peer_count)
    n_raw = len(flagged)
    flagged = cap_flagged(flagged, max_flagged)
    logger.info("Movers: %d flagged (capped to %d)", n_raw, len(flagged))

    if not flagged.empty:
        flagged = enrich_with_news(flagged, finnhub_key)
        flagged = enrich_with_summaries(flagged, anthropic_key, model=llm_model)
    else:
        flagged["_news"] = []
        flagged["_why"] = ""

    return {
        "count": len(flagged),
        "html": render_html(flagged, today, abs_threshold_pct, z_threshold),
        "md": render_markdown(flagged, today, abs_threshold_pct, z_threshold),
        "slack": format_slack_summary(flagged, today, abs_threshold_pct, z_threshold),
        "flagged_df": flagged,
    }
