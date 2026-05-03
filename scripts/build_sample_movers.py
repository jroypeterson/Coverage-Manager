"""One-shot script to generate a sample movers report and post to Slack.

Builds a synthetic perf_df from real tickers in the coverage universe with
plausible synthetic 1W returns: mostly small noise, a handful of sector
z-score outliers, and a few absolute outliers. Pins XE and PS as anchor
movers (they have real recent news that drives sharp Anthropic summaries).

Slack post is prefixed with [SAMPLE] so it's visibly distinct from a real
Friday run.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    API_KEYS, CACHE_DIR, REPORTS_DIR, TODAY,
    MOVERS_ABS_THRESHOLD_PCT, MOVERS_ZSCORE_THRESHOLD,
    MOVERS_MIN_PEER_COUNT, MOVERS_MAX_FLAGGED, MOVERS_LLM_MODEL,
)
from reporting import movers
from reporting.slack import send_slack_notification

random.seed(42)


def build_synthetic_df(csv_path: Path) -> pd.DataFrame:
    """Sample real tickers across sectors, assign synthetic 1W returns."""
    universe = pd.read_csv(csv_path)
    # Trim to columns movers reads
    keep = ["Ticker", "Company Name", "Sector (JP)", "Subsector (JP)"]
    universe = universe[keep].copy()
    universe["Sector (JP)"] = universe["Sector (JP)"].fillna("").astype(str).str.strip()

    # Sample ~12 per sector so each cohort has enough peers for z-score.
    samples = []
    for sector in ["Biopharma", "MedTech", "Healthcare Services", "SaaS", "Tech", "Other"]:
        cohort = universe[universe["Sector (JP)"] == sector]
        n = min(12, len(cohort))
        if n > 0:
            samples.append(cohort.sample(n=n, random_state=hash(sector) % (2**31)))
    df = pd.concat(samples, ignore_index=True)

    # Assign baseline noise
    df["1W"] = [round(random.gauss(mu=0.5, sigma=2.5), 2) for _ in range(len(df))]

    # Inject 2 sector-z outliers per sector with cohort >= 5
    sector_groups = df.groupby("Sector (JP)")
    z_outlier_rows = []
    for sector, group in sector_groups:
        if len(group) >= 5:
            picks = group.sample(n=2, random_state=hash(sector + "z") % (2**31))
            z_outlier_rows.extend(picks.index.tolist())
    # Sector-relative outliers: ~+8% (above cohort mean ~0.5%, with sigma ~2.5
    # so z ~3) — should trip z-flag without tripping abs-flag.
    for idx in z_outlier_rows[: len(z_outlier_rows) // 2]:
        df.at[idx, "1W"] = round(random.uniform(7.5, 9.5), 2)
    for idx in z_outlier_rows[len(z_outlier_rows) // 2 :]:
        df.at[idx, "1W"] = round(random.uniform(-9.5, -7.5), 2)

    # Pin XE (+18.7%) and PS (-14.2%) — real recent IPOs with news in Finnhub.
    pinned = pd.DataFrame([
        {"Ticker": "XE", "Company Name": "X-Energy, Inc.",
         "Sector (JP)": "Other", "Subsector (JP)": "Clean Energy / Nuclear SMR",
         "1W": 18.7},
        {"Ticker": "PS", "Company Name": "Pershing Square Inc.",
         "Sector (JP)": "Other", "Subsector (JP)": "Alt Asset Manager",
         "1W": -14.2},
    ])

    # A few extra absolute movers in major sectors so the report has variety.
    extras = pd.DataFrame([
        # Big up in MedTech (real ticker ISRG)
        {"Ticker": "ISRG", "Company Name": "Intuitive Surgical Inc.",
         "Sector (JP)": "MedTech", "Subsector (JP)": "Robotic Surgery", "1W": 13.4},
        # Big down in Biopharma
        {"Ticker": "BIIB", "Company Name": "Biogen Inc.",
         "Sector (JP)": "Biopharma", "Subsector (JP)": "", "1W": -16.9},
        # Big up in Tech
        {"Ticker": "PANW", "Company Name": "Palo Alto Networks Inc.",
         "Sector (JP)": "Tech", "Subsector (JP)": "", "1W": 11.8},
    ])

    df = pd.concat([df, pinned, extras], ignore_index=True)
    # Drop dupes (in case any pinned/extras already came from sampling)
    df = df.drop_duplicates(subset=["Ticker"], keep="last").reset_index(drop=True)
    return df


def main():
    csv_path = PROJECT_ROOT / "data" / "coverage_universe_tickers.csv"
    df = build_synthetic_df(csv_path)
    print(f"Built synthetic snapshot: {len(df)} tickers across "
          f"{df['Sector (JP)'].nunique()} sectors")

    # Write pickle so cli.py movers can read it (movers_runner expects this path).
    snap_dir = CACHE_DIR / "perf"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"perf_df_{TODAY}.pkl"
    df.to_pickle(snap_path)
    print(f"Saved snapshot: {snap_path}")

    finnhub_key = API_KEYS.get("FINNHUB_API_KEY") or ""
    anthropic_key = API_KEYS.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")

    bundle = movers.run(
        perf_df=df,
        today=TODAY,
        finnhub_key=finnhub_key,
        anthropic_key=anthropic_key,
        abs_threshold_pct=MOVERS_ABS_THRESHOLD_PCT,
        z_threshold=MOVERS_ZSCORE_THRESHOLD,
        min_peer_count=MOVERS_MIN_PEER_COUNT,
        max_flagged=MOVERS_MAX_FLAGGED,
        llm_model=MOVERS_LLM_MODEL,
    )
    print(f"Flagged: {bundle['count']} tickers")

    html_path = REPORTS_DIR / f"coverage_movers_{TODAY}.html"
    md_path = REPORTS_DIR / f"coverage_movers_{TODAY}.md"
    html_path.write_text(bundle["html"], encoding="utf-8")
    md_path.write_text(bundle["md"], encoding="utf-8")
    print(f"Wrote: {html_path}\n       {md_path}")

    # Slack post — prefix with [SAMPLE] header so it's visually distinct
    # from a real Friday run.
    sample_header = (
        ":test_tube: *[SAMPLE — synthetic returns, real news lookups]*\n"
        ":information_source: This is a sample of the new weekly movers "
        "report. The 1W returns are synthetic, but Finnhub news + the Claude "
        "'why' summary are real.\n\n"
    )
    slack_text = sample_header + bundle["slack"]

    webhook = API_KEYS.get("SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print("No SLACK_WEBHOOK_URL set — skipping Slack post")
        return
    ok = send_slack_notification(webhook, slack_text)
    print(f"Slack post: {'sent' if ok else 'FAILED'}")


if __name__ == "__main__":
    main()
