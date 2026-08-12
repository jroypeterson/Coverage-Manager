"""The watchlist buy/target-gap report goes to #portfolio, not #stock-price-alerts.

JP, 2026-08-10: "That table belongs in the portfolio channel, not in price movements:
stop sending it to stock price movements and post it to #portfolio instead."

The trap this pins: `SLACK_WEBHOOK_URL` is an INCOMING WEBHOOK, permanently bound to
the channel it was minted for. `reporting.slack.SLACK_CHANNEL` is only a log label, so
"move the report" could never be done by editing a channel string, and any fallback to
that webhook silently re-posts to the channel he asked us to leave.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pandas as pd
import pytest

from reporting import slack as slack_mod
from reporting import watchlist_report as wr


@pytest.fixture
def df():
    return pd.DataFrame([{
        "Ticker": "AAPL", "Currency": "USD", "Current Price": 100.0,
        "Buy Price": 90.0, "Target Price": 130.0,
        "% vs Buy": 11.1, "% vs Target": -23.1,
    }])


class TestRouting:
    def test_it_posts_with_the_bot_token_to_the_configured_channel(self, df, monkeypatch):
        sent = {}

        def fake_post(token, channel, text, timeout=20):
            sent.update(token=token, channel=channel, text=text)
            return True

        monkeypatch.setattr(wr, "post_to_channel", fake_post)
        assert wr.post_watchlist_to_slack(
            df, "2026-08-11",
            {"SLACK_BOT_TOKEN": "xoxb-t", "SLACK_PORTFOLIO_CHANNEL_ID": "C0B1CM66T19"},
        ) is True
        assert sent["token"] == "xoxb-t"
        assert sent["channel"] == "C0B1CM66T19"
        assert "AAPL" in sent["text"] and "tgt" in sent["text"]

    def test_it_never_falls_back_to_the_old_webhook(self, df, monkeypatch):
        """The failure mode worth a test: an unconfigured bot path quietly reverting
        to #stock-price-alerts would look exactly like success."""
        called = []
        monkeypatch.setattr(slack_mod, "send_slack_notification",
                            lambda *a, **k: called.append(a) or True)
        monkeypatch.setattr(wr, "post_to_channel",
                            lambda *a, **k: pytest.fail("should not post"))
        assert wr.post_watchlist_to_slack(
            df, "2026-08-11", {"SLACK_WEBHOOK_URL": "https://hooks.slack/OLD"}) is False
        assert called == [], "the old #stock-price-alerts webhook was used"

    def test_a_failed_post_does_not_raise(self, df, monkeypatch):
        """The report is already written and emailed by this point."""
        def boom(*a, **k):
            raise slack_mod.SlackPostError("chat.postMessage failed: not_in_channel")
        monkeypatch.setattr(wr, "post_to_channel", boom)
        assert wr.post_watchlist_to_slack(
            df, "2026-08-11",
            {"SLACK_BOT_TOKEN": "t", "SLACK_PORTFOLIO_CHANNEL_ID": "C"}) is False

    def test_the_summary_still_carries_price_buy_and_target_with_gaps(self, df):
        text = wr.format_slack_summary(df, "2026-08-11")
        assert "buy" in text and "tgt" in text
        assert "11.1" in text and "-23.1" in text, "the gap columns are the point"


class TestPostToChannel:
    def _resp(self, body):
        class R:
            status = 200
            def read(self_inner):
                return json.dumps(body).encode()
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
        return R()

    def test_it_raises_on_a_slack_level_failure(self, monkeypatch):
        """ok:false with HTTP 200 is Slack's normal failure shape. Returning False and
        letting the caller shrug is how a report goes missing behind a green run."""
        monkeypatch.setattr(slack_mod, "urlopen_with_retry",
                            lambda *a, **k: self._resp({"ok": False,
                                                        "error": "not_in_channel"}))
        with pytest.raises(slack_mod.SlackPostError) as e:
            slack_mod.post_to_channel("t", "C", "hi")
        assert "not_in_channel" in str(e.value)
        assert "invite the bot" in str(e.value), "the error must name its own fix"

    def test_it_succeeds_on_ok_true(self, monkeypatch):
        monkeypatch.setattr(slack_mod, "urlopen_with_retry",
                            lambda *a, **k: self._resp({"ok": True, "ts": "1.2"}))
        assert slack_mod.post_to_channel("t", "C", "hi") is True

    def test_the_other_lanes_keep_their_webhook_sender(self):
        """movers_runner and weekly_build still post via the webhook; this change must
        not have removed the function underneath them."""
        assert callable(slack_mod.send_slack_notification)
        assert slack_mod.SLACK_CHANNEL == "#stock-price-alerts"
        assert slack_mod.POSITIONS_CHANNEL == "#portfolio"
