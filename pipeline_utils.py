"""Shared helpers for pipeline orchestrators (weekly_universe, weekly_report, weekly_build)."""

from logging_utils import get_logger

logger = get_logger("pipeline")


def run_step(name, fn, *args, **kwargs):
    """Run a pipeline step, catching and logging failures.

    Returns (status_string, result_or_none). On success, status is "ok".
    On failure, status is "failed: <exception message>".
    """
    try:
        result = fn(*args, **kwargs)
        return "ok", result
    except Exception as e:
        logger.warning("Step '%s' failed: %s", name, e)
        return f"failed: {e}", None


def collect_non_successes(steps):
    """Return the list of step names whose status indicates non-success.

    Non-success covers both:
      - "failed: ..." — the step raised an exception
      - "blocked: ..." — the step was prevented from running by a gating
        decision (e.g. validation failed and --force was not passed)

    Both are operationally non-success: a blocked report run produced no
    report, just like a failed run did. Distinguishing them is for debugging
    only and the prefix is preserved in the status string.

    Statuses like "ok", "skipped", "skipped (dry run)", and "skipped: <reason>"
    are NOT non-successes — they're either healthy or deliberate operator
    choices.
    """
    return [
        k
        for k, v in steps.items()
        if isinstance(v, str) and (v.startswith("failed") or v.startswith("blocked"))
    ]
