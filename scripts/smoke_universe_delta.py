"""One-off smoke test for the universe-delta -> #coverage Slack post.

Constructs a synthetic delta with representative content (added/removed/
modified/position-changes) and fires it at the real SLACK_WEBHOOK_COVERAGE
so the user can verify rendering before Friday's scheduled run.

Header is marked as a smoke test so the message can't be confused with a
real weekly post. Safe to re-run; safe to delete after verification.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import API_KEYS
from reporting import universe_delta as ud


def main():
    delta = {
        "added": [
            {
                "ticker": "NEWA", "name": "Newco Therapeutics",
                "sector": "Biopharma", "subsector": "Biotech",
                "sub_subsector": "Oncology", "country_hq": "United States",
                "core": "",
            },
            {
                "ticker": "NEWB", "name": "Newco Diagnostics",
                "sector": "MedTech", "subsector": "Diagnostics",
                "sub_subsector": "", "country_hq": "United States",
                "core": "",
            },
        ],
        "removed": [
            {
                "ticker": "ADVM", "name": "Adverum Biotechnologies",
                "sector": "Biopharma", "subsector": "",
                "reason": "Acquired — Acquired by Eli Lilly for $3.56 + CVR",
            },
        ],
        "modified": [
            {"ticker": "FOO", "field": "Sector (JP)", "old": "Other", "new": "Biopharma"},
            {"ticker": "FOO", "field": "Subsector (JP)", "old": "", "new": "Oncology"},
            {"ticker": "BAR", "field": "Core", "old": "", "new": "Y"},
        ],
        "position_changes": [
            {"ticker": "BIIB", "before_state": "Researching", "after_state": "Portfolio"},
            {"ticker": "RPRX", "before_state": "Following for Interest", "after_state": "Ready to Buy"},
        ],
        "before_stats": {
            "total": 1093, "core_y": 263,
            "sector_counts": {
                "Biopharma": 705, "MedTech": 142, "Healthcare Services": 106,
                "SaaS": 56, "Tech": 52,
            },
        },
        "after_stats": {
            "total": 1094, "core_y": 264,
            "sector_counts": {
                "Biopharma": 706, "MedTech": 143, "Healthcare Services": 106,
                "SaaS": 56, "Tech": 52,
            },
        },
        "before_position_counts": {
            "Portfolio": 18, "Researching": 24, "Following for Interest": 11,
            "Ready to Buy": 5, "Ready to Short": 2,
        },
        "after_position_counts": {
            "Portfolio": 19, "Researching": 23, "Following for Interest": 10,
            "Ready to Buy": 6, "Ready to Short": 2,
        },
        "baseline_sha": "3b975ef0000000000000000000000000",
        "baseline_date": "2026-05-22",
        # New v2 fields — snapshot-source semantics + dirty-tree caveat slot.
        # For the smoke test we simulate the normal happy path: snapshot baseline,
        # no caveat. To preview the dirty-tree warning, set baseline_caveat to
        # a non-empty string.
        "baseline_source": "snapshot",
        "baseline_label": "end of previous run · 2026-05-22",
        "baseline_caveat": None,
        # Header explicitly marks this as a smoke test so the message can't be
        # mistaken for a real Friday post.
        "today": "SMOKE TEST (delete this) — 2026-05-26",
    }

    webhook = API_KEYS.get("SLACK_WEBHOOK_COVERAGE")
    if not webhook:
        print("SLACK_WEBHOOK_COVERAGE not configured in .env")
        sys.exit(1)

    result = ud.post_universe_delta(webhook, delta)
    print(f"posted={result['posted']}  reason={result['reason']}")
    sys.exit(0 if result["posted"] else 2)


if __name__ == "__main__":
    main()
