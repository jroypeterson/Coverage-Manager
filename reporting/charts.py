"""Chart rendering for the performance report.

Thin, side-effect-only matplotlib rendering (the Agg backend, no display) —
the axis math lives in `reporting/calcs.py` so it stays unit-testable without
rendering. Currently: the P/E vs forward-2yr-EPS-growth scatter for the
positions/research (Phase 1) set.
"""

import math

import matplotlib

matplotlib.use("Agg")  # headless — no display, write PNG only
import matplotlib.pyplot as plt  # noqa: E402

from logging_utils import get_logger  # noqa: E402

logger = get_logger("reporting.charts")

# A stable, distinguishable palette keyed by Sector (JP).
_SECTOR_COLORS = {
    "Biopharma": "#1f77b4",
    "MedTech": "#2ca02c",
    "Healthcare Services": "#d62728",
    "SaaS": "#9467bd",
    "Tech": "#8c564b",
    "Financials": "#e377c2",
    "Industrials": "#7f7f7f",
    "Consumer": "#bcbd22",
    "Energy": "#17becf",
    "Materials": "#aec7e8",
    "Real Estate": "#ff9896",
}
_DEFAULT_COLOR = "#ff7f0e"


def _marker_size(mkt_cap_usd):
    """Dot area scaled by market cap (USD). sqrt keeps mega-caps from swamping
    the plot; clamped so a missing/zero cap still renders a visible point."""
    if not mkt_cap_usd or mkt_cap_usd <= 0:
        return 40.0
    billions = mkt_cap_usd / 1e9
    return max(40.0, min(900.0, 40.0 + 32.0 * math.sqrt(billions)))


def render_pe_growth_scatter(rows, out_path, title="P/E (TTM) vs Forward 2-Year EPS Growth"):
    """Render a labeled scatter of P/E (y) vs forward 2-yr EPS-growth %/yr (x).

    `rows`: iterable of dicts with keys `ticker`, `pe`, `growth` (required,
    non-None), and optional `sector`, `mkt_cap` (USD). Rows missing pe/growth
    are skipped by the caller. Dots are sized by market cap, colored by sector,
    and labeled with the ticker. Median guide-lines split the plane into the
    cheap/expensive × low/high-growth quadrants. Writes a PNG to `out_path` and
    returns the count of points plotted (0 → no file written)."""
    pts = [
        r for r in rows
        if r.get("pe") is not None and r.get("growth") is not None
        and not (isinstance(r["pe"], float) and math.isnan(r["pe"]))
        and not (isinstance(r["growth"], float) and math.isnan(r["growth"]))
    ]
    if not pts:
        logger.warning("P/E-vs-growth scatter: no points with both P/E and growth — skipping")
        return 0

    fig, ax = plt.subplots(figsize=(13, 9))
    seen_sectors = {}
    for r in pts:
        sector = r.get("sector") or "Other"
        color = _SECTOR_COLORS.get(sector, _DEFAULT_COLOR)
        sc = ax.scatter(
            r["growth"], r["pe"],
            s=_marker_size(r.get("mkt_cap")),
            c=color, alpha=0.6, edgecolors="black", linewidths=0.5,
            label=sector if sector not in seen_sectors else None,
        )
        seen_sectors[sector] = sc
        ax.annotate(
            r["ticker"], (r["growth"], r["pe"]),
            fontsize=7, xytext=(3, 3), textcoords="offset points",
        )

    # Median guide-lines → cheap/expensive × low/high-growth quadrants.
    pes = sorted(p["pe"] for p in pts)
    grs = sorted(p["growth"] for p in pts)
    med_pe = pes[len(pes) // 2]
    med_gr = grs[len(grs) // 2]
    ax.axhline(med_pe, color="grey", linestyle="--", linewidth=0.8, alpha=0.6)
    ax.axvline(med_gr, color="grey", linestyle="--", linewidth=0.8, alpha=0.6)

    ax.set_xlabel("Forward 2-year EPS growth (annualized %, FMP analyst estimates)")
    ax.set_ylabel("P/E (TTM)")
    ax.set_title(f"{title}  ·  n={len(pts)}")
    ax.grid(True, linestyle=":", alpha=0.3)
    if seen_sectors:
        ax.legend(loc="upper left", fontsize=8, title="Sector (JP)", framealpha=0.9)
    fig.text(
        0.99, 0.01,
        "Dot size ∝ market cap · dashed lines = medians · "
        "lower-right quadrant = cheap + high-growth",
        ha="right", va="bottom", fontsize=7, color="#888",
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    logger.info("Wrote P/E-vs-growth scatter (%s points) to %s", len(pts), out_path)
    return len(pts)
