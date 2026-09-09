"""An out-of-credit Anthropic account must not be swallowed (board #330).

On 2026-08-20 at 00:02 an overnight session hit *"Your credit balance is too low
to access the Anthropic API"*. Every lane in this fleet that calls Claude fails
at once on an empty account, and nothing checks the balance -- so the condition
arrives inside each lane and is reported as that lane's own bug.

This module was the worst of them, because it did not report anything at all:
`except anthropic.APIStatusError: return ""` turned the account being empty into
a blank `_why` column, and the movers run still reported `ok`. The weekly report
would ship without its explanations, looking entirely healthy.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic

from providers import anthropic_summary as A


# A headline in the shape the function actually consumes (dicts, not strings).
HEADLINE = {"date": "2026-08-19", "source": "Reuters",
            "headline": "Apple falls on guidance"}

# The vendor's own sentence, as received on 2026-08-20.
REAL = ("Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits.")


@pytest.mark.parametrize("msg", [
    REAL,
    "your credit balance is too low",                 # case
    "Please go to Plans & Billing to purchase credits.",
    "Insufficient credit on this organization.",      # a plausible reword
])
def test_the_billing_wording_is_recognised(msg):
    assert A.is_billing_error(Exception(msg))


@pytest.mark.parametrize("msg", [
    "Number of requests has exceeded your rate limit",
    "messages.0.content: field required",
    "Overloaded",
    "temperature: Extra inputs are not permitted",
])
def test_an_ordinary_api_error_is_NOT_a_billing_error(msg):
    """The other side of the classifier. A guard that fires on everything would
    turn every transient hiccup into a failed weekly report."""
    assert not A.is_billing_error(Exception(msg))


def _boom(exc):
    class _Msgs:
        def create(self, **kw):
            raise exc

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()
    return _Client


def _status_error(msg):
    """An APIStatusError shaped like the real one, without a live HTTP call.

    Built through the SDK's real constructor with a real httpx response, so the
    test exercises the class production actually raises rather than a stand-in
    that merely subclasses it.
    """
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req, json={
        "type": "error",
        "error": {"type": "invalid_request_error", "message": msg},
    })
    return anthropic.APIStatusError(
        msg, response=resp,
        body={"type": "error",
              "error": {"type": "invalid_request_error", "message": msg}})


def test_a_credit_error_RAISES_instead_of_returning_empty(monkeypatch):
    """⛑ THE REGRESSION. It returned "" and the run reported ok.

    Raising routes it through `run_step` -> `failed:` -> a `partial` heartbeat,
    which is the fleet's designed path for "this ran and could not do its job",
    and it stops the loop rather than making twenty-nine more doomed calls.
    """
    monkeypatch.setattr(A.anthropic, "Anthropic", _boom(_status_error(REAL)))
    with pytest.raises(A.AnthropicCreditExhausted):
        A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                         weekly_pct=-12.0, headlines=[HEADLINE], api_key="sk-test")


def test_an_ordinary_status_error_STILL_degrades_gracefully(monkeypatch):
    """The module's documented behaviour for a per-ticker failure is unchanged:
    one bad row must not fail the whole weekly report."""
    monkeypatch.setattr(A.anthropic, "Anthropic",
                        _boom(_status_error("messages.0: field required")))
    assert A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                            weekly_pct=-12.0, headlines=[HEADLINE],
                            api_key="sk-test") == ""


def test_a_credit_error_reaching_the_CATCH_ALL_still_raises(monkeypatch):
    """The SDK does not guarantee which class carries this. A credit error that
    arrived as a bare Exception would otherwise be swallowed exactly as before."""
    monkeypatch.setattr(A.anthropic, "Anthropic", _boom(RuntimeError(REAL)))
    with pytest.raises(A.AnthropicCreditExhausted):
        A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                         weekly_pct=-12.0, headlines=[HEADLINE], api_key="sk-test")
