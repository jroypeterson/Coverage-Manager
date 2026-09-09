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


# ── Codex round 1 (2026-09-08): a defect in the first version of this fix ────

def _auth_error(msg):
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(401, request=req, json={"error": {"message": msg}})
    return anthropic.AuthenticationError(msg, response=resp, body=None)


def _rate_limit_error(msg):
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(429, request=req, json={"error": {"message": msg}})
    return anthropic.RateLimitError(msg, response=resp, body=None)


def test_the_subclass_handlers_are_not_a_way_past_the_billing_check():
    """⛑ Both SUBCLASS `APIStatusError`, and Python matches `except` in order.

    The first version of this fix put the billing check in the `APIStatusError`
    clause -- third, behind `AuthenticationError` and `RateLimitError` -- so a
    credit message arriving as either of those was swallowed exactly as before,
    and the "belt and braces" catch-all was unreachable for them. Found by Codex
    reviewing the fix, not by the tests written alongside it.
    """
    assert issubclass(anthropic.AuthenticationError, anthropic.APIStatusError)
    assert issubclass(anthropic.RateLimitError, anthropic.APIStatusError)


@pytest.mark.parametrize("make", [_auth_error, _rate_limit_error])
def test_a_billing_message_wearing_a_subclass_still_raises(monkeypatch, make):
    """Reproduced before the fix: both returned ""."""
    monkeypatch.setattr(A.anthropic, "Anthropic", _boom(make(REAL)))
    with pytest.raises(A.AnthropicCreditExhausted):
        A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                         weekly_pct=-12.0, headlines=[HEADLINE], api_key="sk-test")


@pytest.mark.parametrize("make,msg", [
    (_auth_error, "invalid x-api-key"),
    (_rate_limit_error, "Number of requests has exceeded your rate limit"),
])
def test_an_ORDINARY_subclass_error_still_degrades_gracefully(monkeypatch, make, msg):
    """The other side: the widened check must not turn every auth blip or rate
    limit into a failed weekly report."""
    monkeypatch.setattr(A.anthropic, "Anthropic", _boom(make(msg)))
    assert A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                            weekly_pct=-12.0, headlines=[HEADLINE],
                            api_key="sk-test") == ""


def test_the_raised_message_CARRIES_the_vendor_wording(monkeypatch):
    """⛑ Codex mutation: raising a tidy summary instead of `str(e)` passes a
    type-only assertion while the fleet heartbeat classifier -- which greps the
    heartbeat text for the vendor's phrases -- loses the thing it matches on.
    The exception text is an interface here, not a message to a human.
    """
    monkeypatch.setattr(A.anthropic, "Anthropic", _boom(_status_error(REAL)))
    with pytest.raises(A.AnthropicCreditExhausted) as ei:
        A.summarize_move(ticker="AAPL", company="Apple", sector="Tech",
                         weekly_pct=-12.0, headlines=[HEADLINE], api_key="sk-test")
    assert "credit balance" in str(ei.value).lower()


@pytest.mark.parametrize("phrase", [
    "credit balance", "plans & billing", "plans and billing",
    "purchase credits", "insufficient credit",
])
def test_every_declared_phrase_is_matched_on_its_own(phrase):
    """⛑ Codex mutation: deleting `plans and billing` from the list passed,
    because no test exercised the ampersand-less spelling in isolation. Each
    phrase is a separate hedge against a rewording and must be pinned alone."""
    assert A.is_billing_error(Exception(f"...{phrase}..."))
