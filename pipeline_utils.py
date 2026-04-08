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


def collect_failures(steps):
    """Return the list of step names whose status starts with 'failed'."""
    return [k for k, v in steps.items() if isinstance(v, str) and v.startswith("failed")]
