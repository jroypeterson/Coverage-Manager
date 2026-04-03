"""Generate the weekly coverage prompt with injected universe stats.

The weekly_coverage_prompt.md file remains the source of truth for Claude's
instructions. This module injects current universe statistics so Claude
always has fresh context.
"""

import json
from pathlib import Path

import pandas as pd

from config import SCRIPT_DIR, CSV_PATH, DATA_DIR, TODAY
from logging_utils import get_logger

logger = get_logger("discovery.prompt_template")

TEMPLATE_PATH = SCRIPT_DIR / "weekly_coverage_prompt.md"


def _get_universe_stats(csv_path=None):
    """Compute summary stats from the coverage universe CSV."""
    csv_path = csv_path or CSV_PATH
    df = pd.read_csv(csv_path)

    sector_col = "Sector (JP)" if "Sector (JP)" in df.columns else "Sector"
    sector_counts = df[sector_col].value_counts().to_dict() if sector_col in df.columns else {}

    return {
        "total_tickers": len(df),
        "date": TODAY,
        "sector_breakdown": sector_counts,
        "exchanges": df["Exchange"].dropna().nunique() if "Exchange" in df.columns else 0,
    }


def generate_prompt(template_path=None, csv_path=None):
    """Read the prompt template and append current universe context.

    Returns the full prompt text with stats injected at the end.
    """
    template_path = Path(template_path) if template_path else TEMPLATE_PATH
    csv_path = csv_path or CSV_PATH

    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8")
    stats = _get_universe_stats(csv_path)

    # Build stats block
    sector_lines = "\n".join(f"  - {k}: {v}" for k, v in sorted(stats["sector_breakdown"].items(), key=lambda x: -x[1]))
    stats_block = f"""

---

## Current Universe Context (auto-generated {stats['date']})

- Total tickers in coverage: {stats['total_tickers']}
- Unique exchanges: {stats['exchanges']}
- Sector breakdown:
{sector_lines}

Discovery input JSON with full ticker/company list is at:
`Coverage Manager/data/discovery_input_{stats['date']}.json`

Save discovery output as:
`Coverage Manager/data/discovery_output_{stats['date']}.json`
(Must conform to `Coverage Manager/discovery/discovery_output_schema.json`)
"""

    return template_text + stats_block


def save_prompt(output_path=None, **kwargs):
    """Generate and save the prompt to a file.

    Returns the output path.
    """
    output_path = Path(output_path) if output_path else (DATA_DIR / f"weekly_prompt_{TODAY}.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = generate_prompt(**kwargs)
    output_path.write_text(prompt, encoding="utf-8")

    logger.info("Generated prompt saved to %s", output_path)
    return output_path
