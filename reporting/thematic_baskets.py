"""Thematic stock baskets — cap- and equal-weighted returns from CM caches.

JP's 2026-07-08 ask: track thematic baskets (the AI trade, GLP-1 winners/losers,
obesity, Alzheimer's, MRD, oncology) individually and collectively, computing
market-cap-weighted and equal-weighted returns over WTD / QTD / 2026-YTD / 2025.

Reads the latest performance snapshot pickle (`cache/perf/perf_df_<date>.pkl`,
written by `cli.py performance`) — the same rich per-ticker frame the movers
report consumes — so this re-fetches nothing. Each row already carries `Mkt Cap`,
`Sector (JP)`, and per-period returns (`1W`, `QTD`, `YTD`, and calendar-year
columns incl. `2025`). Output: a readable table in `reports/`.

⚠ BASKET MEMBERSHIP IS A JUDGMENT CALL (the scoping JP invited). The lists below
are a documented v1 — edit `BASKETS` to refine. Themes like "AI trade" and
"GLP-1 losers" span sectors and aren't a clean Sector/Subsector filter, so they
are explicit ticker lists. Names not in the coverage universe are reported as
"missing" per basket (e.g. much of the AI trade is outside CM's HC-focused
universe) so coverage gaps are visible, not silent.

Entry point: `build()` → writes `reports/thematic_baskets_<perf-date>.md`,
returns the path. Wired as the `baskets` CLI subcommand.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_PERF_DIR = _ROOT / "cache" / "perf"
_OUT = _ROOT / "reports"

# Period column in perf_df -> display label. WTD uses the 1-week return; 2025 is
# the calendar-year column.
_PERIODS = [("1W", "WTD"), ("QTD", "QTD"), ("YTD", "2026 YTD"), ("2025", "2025 FY")]

# v1 membership — EDIT to refine (JP invited scoping). Intended names not yet in
# the coverage universe are kept here on purpose; the report flags them as missing.
BASKETS: dict[str, list[str]] = {
    "AI Trade": ["NVDA", "AVGO", "AMD", "TSM", "MSFT", "GOOGL", "META", "AMZN",
                 "PLTR", "ANET", "MRVL", "MU", "ASML", "ORCL", "DELL", "SMCI",
                 "VRT", "CRWD", "NOW", "SNOW"],
    "GLP-1 Winners": ["LLY", "NVO"],
    "GLP-1 Losers": ["DXCM", "PODD", "ZBH", "DVA", "BAX", "TNDM", "ISRG"],
    "Obesity": ["LLY", "NVO", "VKTX", "AMGN", "TERN", "STOK", "ALT", "GPCR"],
    "Alzheimer's": ["BIIB", "LLY", "AXSM", "ACAD", "ANVS", "PRAX", "CRVS", "SAVA"],
    "MRD": ["NTRA", "GH", "ADPT", "TXG", "MYGN", "NVTA"],
    "Oncology": ["MRK", "PFE", "BMY", "AZN", "GILD", "EXEL", "RVMD", "SMMT",
                 "ARVN", "DAWN", "IDYA", "RXRX"],
}


def latest_perf_pickle() -> Path:
    """Newest `perf_df_<date>.pkl` (date-named, so lexical max = latest)."""
    pkls = sorted(_PERF_DIR.glob("perf_df_*.pkl"))
    if not pkls:
        raise FileNotFoundError(
            f"No perf_df_*.pkl in {_PERF_DIR} — run `cli.py performance` first.")
    return pkls[-1]


def _weighted(returns: pd.Series, caps: pd.Series):
    """(equal_weighted, cap_weighted) mean return, NaN-safe. Percent in, percent out."""
    r = pd.to_numeric(returns, errors="coerce")
    c = pd.to_numeric(caps, errors="coerce")
    ew = r.dropna().mean() if r.notna().any() else float("nan")
    mask = r.notna() & c.notna() & (c > 0)
    cw = (r[mask] * c[mask]).sum() / c[mask].sum() if mask.any() else float("nan")
    return ew, cw


def compute_baskets(df: pd.DataFrame) -> list[dict]:
    """One record per basket: membership coverage + EW/CW return per period."""
    frame = df.set_index("Ticker") if "Ticker" in df.columns else df
    universe = set(frame.index)
    out = []
    for name, tickers in BASKETS.items():
        present = [t for t in tickers if t in universe]
        missing = [t for t in tickers if t not in universe]
        sub = frame.loc[present]
        periods = {}
        for col, label in _PERIODS:
            if col in sub.columns:
                ew, cw = _weighted(sub[col], sub["Mkt Cap"])
            else:
                ew = cw = float("nan")
            periods[label] = (ew, cw)
        out.append({"name": name, "present": present, "missing": missing,
                    "n": len(present), "periods": periods})
    return out


def _fmt(v) -> str:
    return "n/a" if v != v else f"{v:+.1f}%"  # v!=v catches NaN


def render_markdown(records: list[dict], perf_date: str) -> str:
    period_labels = [lbl for _c, lbl in _PERIODS]
    lines = [
        "# Thematic stock baskets — cap- & equal-weighted returns",
        "",
        f"_Basket returns from the Coverage Manager performance snapshot "
        f"(`perf_df_{perf_date}.pkl`). **EW** = equal-weighted, **CW** = "
        f"market-cap-weighted. `n` = basket members present in the coverage "
        f"universe._",
        "",
        "| Basket | n | " + " | ".join(f"{p} (EW / CW)" for p in period_labels) + " |",
        "|" + "---|" * (len(period_labels) + 2),
    ]
    for rec in records:
        cells = [f"{_fmt(rec['periods'][p][0])} / {_fmt(rec['periods'][p][1])}"
                 for p in period_labels]
        lines.append(f"| {rec['name']} | {rec['n']} | " + " | ".join(cells) + " |")
    lines += ["", "## Basket membership & coverage gaps", ""]
    for rec in records:
        present = ", ".join(rec["present"]) or "_none in universe_"
        line = f"- **{rec['name']}** ({rec['n']}): {present}"
        if rec["missing"]:
            line += f"  · _not in universe: {', '.join(rec['missing'])}_"
        lines.append(line)
    lines += [
        "",
        "_⚠ v1 basket membership is a judgment call (see `reporting/thematic_baskets.py` "
        "`BASKETS`). Themes like the AI trade and GLP-1 losers span sectors and are "
        "curated ticker lists — refine them there. Names flagged \"not in universe\" "
        "would need adding to the coverage universe (e.g. much of the AI trade is "
        "outside CM's HC-focused coverage). Regenerate: `python cli.py baskets`._",
    ]
    return "\n".join(lines) + "\n"


def build() -> Path:
    """Compute basket returns from the latest perf snapshot and write the report."""
    pkl = latest_perf_pickle()
    perf_date = pkl.stem.replace("perf_df_", "")
    df = pd.read_pickle(pkl)
    records = compute_baskets(df)
    md = render_markdown(records, perf_date)
    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"thematic_baskets_{perf_date}.md"
    path.write_text(md, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(build())
