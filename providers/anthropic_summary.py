"""Anthropic Claude helper for the movers report.

Given a ticker's weekly move and recent news headlines, returns a 2-3 line
"why" explanation. Falls back to an empty string if ANTHROPIC_API_KEY is
unset or the API errors — the movers report degrades to a headline-list-only
view in that case.

Uses Claude Haiku 4.5 by default (cheap and fast for short structured
summaries). The system prompt is large and stable across calls in a single
movers run, so we attach a cache_control breakpoint to amortize cost over
the ~5-30 flagged tickers per week. (Caching only takes effect if the
system prompt clears the 4096-token minimum prefix; otherwise the request
silently runs uncached, which is fine.)
"""

import anthropic

from logging_utils import get_logger, log_exception

logger = get_logger("providers.anthropic_summary")


# Cached system prompt. Stable across all per-ticker calls in a movers run.
# Padded with worked examples to give the cache prefix a real shot at the
# 4096-token Haiku 4.5 minimum; if it falls short the request still runs,
# just at full input cost.
SYSTEM_PROMPT = """You are an investment analyst summarizing why a single public stock had an extreme weekly price move.

You are given:
1. The ticker, company name, sector, and the size of the weekly move (signed % return).
2. A list of recent news headlines for that ticker, drawn from a 7-day window.

Your job: write a 2-3 line factual explanation of what most likely caused the move, drawing only on the headlines provided. If the headlines do not support a clear cause, say so.

Output rules:
- Plain text. No markdown, no headers, no bullet points.
- Maximum 3 sentences, ~60 words.
- Lead with the most likely cause (earnings, M&A, FDA action, guidance, lawsuit, sector beta, index inclusion, etc.).
- Be specific: name dollar amounts, dates, deal partners, drug names, percentages from the headlines when present.
- Do not editorialize. Do not include a recommendation. Do not speculate beyond what the headlines support.
- If the headlines look unrelated to the move (e.g. only routine analyst notes for a +25% week), say "no clear catalyst in the news window — possibly sector- or flow-driven" or similar.
- Never invent facts that are not in the headlines.

Worked example 1 — clear catalyst:
Inputs: NVDA / NVIDIA Corporation / Tech / +12.4% / headlines: "NVIDIA reports Q2 revenue $30B, beats by $2B"; "NVIDIA guides Q3 above consensus on data center strength"; "Wall Street raises price targets after NVDA print".
Output: NVIDIA reported Q2 revenue of $30B (above consensus by $2B) and guided Q3 above expectations on continued data center demand. Sell-side raised price targets after the print, fueling the +12% move.

Worked example 2 — clear negative catalyst:
Inputs: SLAB / Silicon Labs / Tech / -18.2% / headlines: "Silicon Labs guides Q3 revenue below consensus, cites IoT inventory correction"; "SLAB shares plunge on weak guide".
Output: Silicon Labs guided Q3 revenue below consensus, citing an ongoing IoT inventory correction. The weak forward guide drove the -18% move.

Worked example 3 — no clear catalyst:
Inputs: ACME / Acme Industries / Other / +14.8% / headlines: "Routine 10-Q filing"; "Analyst at Firm X reiterates Hold".
Output: No clear catalyst in the news window — possibly sector- or flow-driven. The only filings in the period were a routine 10-Q and a reiteration of a Hold rating.

Worked example 4 — M&A catalyst:
Inputs: XYZ / XYZ Therapeutics / Biopharma / +42.0% / headlines: "BigPharma announces $4B acquisition of XYZ Therapeutics at $85/share"; "XYZ board approves takeout".
Output: BigPharma announced an agreed $4B acquisition of XYZ Therapeutics at $85/share, approved by the XYZ board. The +42% move reflects the takeout premium relative to last week's close.

Worked example 5 — FDA catalyst:
Inputs: BIOX / Biox Pharma / Biopharma / -55.1% / headlines: "FDA issues Complete Response Letter for Biox lead drug"; "Biox to evaluate path forward after CRL".
Output: The FDA issued a Complete Response Letter on Biox's lead drug, refusing approval at this cycle. Management said it will evaluate next steps; the stock lost more than half its value on the rejection.

Worked example 6 — sector-relative move with thin coverage:
Inputs: SMR / SMR Energy / Other / +9.4% / headlines: "Nuclear sector rallies on data center power demand"; "SMR mentioned in DOE clean energy briefing".
Output: SMR moved alongside a broader nuclear sector rally tied to data center power demand and a DOE clean energy briefing. No company-specific catalyst beyond the macro/sector tape was reported.

Use the same crisp tone for every ticker."""


def summarize_move(
    ticker: str,
    company: str,
    sector: str,
    weekly_pct: float,
    headlines: list,
    api_key: str = "",
    model: str = "claude-haiku-4-5",
    max_tokens: int = 220,
) -> str:
    """Return a 2-3 line "why" summary for a single flagged ticker.

    Args:
        ticker: Stock ticker symbol.
        company: Full company name.
        sector: Sector (JP) classification.
        weekly_pct: 1-week return as a signed percent (e.g. -12.5 for -12.5%).
        headlines: List of dicts with keys ``date``, ``headline``, ``source``,
            ``summary``. Empty list is acceptable.
        api_key: Anthropic API key. Empty string disables the call and
            returns "" — caller should fall back to a headline list.
        model: Claude model to use. Defaults to Haiku 4.5.
        max_tokens: Output token cap.

    Returns the summary string, or "" on any failure (caller decides how to
    handle).
    """
    if not api_key:
        return ""
    if not headlines:
        return ""

    headline_lines = []
    for h in headlines[:8]:
        date_part = h.get("date") or ""
        source_part = h.get("source") or ""
        text_part = (h.get("headline") or "").strip()
        if not text_part:
            continue
        headline_lines.append(f"- [{date_part} / {source_part}] {text_part}")

    if not headline_lines:
        return ""

    sign = "+" if weekly_pct >= 0 else ""
    user_prompt = (
        f"Ticker: {ticker}\n"
        f"Company: {company}\n"
        f"Sector: {sector or 'Unknown'}\n"
        f"Weekly move: {sign}{weekly_pct:.1f}%\n"
        f"\n"
        f"Recent headlines (newest first):\n" + "\n".join(headline_lines)
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.AuthenticationError:
        logger.warning("Anthropic auth failed — check ANTHROPIC_API_KEY")
        return ""
    except anthropic.RateLimitError:
        logger.warning("Anthropic rate limited for %s; skipping summary", ticker)
        return ""
    except anthropic.APIStatusError as e:
        logger.warning("Anthropic API status error for %s: %s", ticker, e)
        return ""
    except Exception as e:
        log_exception(logger, f"Anthropic summary failed for {ticker}", e)
        return ""

    text_blocks = [b.text for b in response.content if b.type == "text"]
    summary = " ".join(text_blocks).strip()
    return summary
