"""Build the 2026-09-11 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft_0904.py. Safe to delete after the run.

Subject follows CONVENTIONS.md section 5: `[ClaudeFin] <project> - <what> - <date>`.
Built by hand rather than through _shared/email_alert, so the whole subject is written here.
"""
import imaplib
import os
import time
from email.mime.text import MIMEText
from email.utils import formatdate

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
ADDR = os.environ["GMAIL_ADDRESS"]
PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
DATE = "2026-09-11"
SUBJECT = f"[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — {DATE}"

# Company, Ticker, Exchange, Mkt Cap, Sector, Subsector, Listing date, Trigger, Peers, Reason
RECS = [
    ("SoundHound AI, Inc.", "SOUN", "NASDAQ", "$2.74B (FMP) / <b>$2.79B</b> (yfinance)",
     "SaaS", "Conversational AI", "2022-04-28 (listed); LPSN deal closed 2026-09-04",
     "New candidate (Bucket 4 &mdash; QUEUED, reply <code>add SOUN</code>)",
     "LPSN (acquired by SOUN), U, TWLO, NICE",
     "SoundHound closed its acquisition of <b>LivePerson (LPSN), a covered SaaS row</b>, on 2026-09-04 "
     "&mdash; 37M Class A shares issued, LivePerson debt retired at close. The enterprise digital-messaging "
     "business the universe tracked through LPSN now sits inside SOUN, so declining this leaves the exposure "
     "uncovered rather than unchanged. Q2 2026 revenue $61.9M (+45% y/y, +40% q/q) at a 58% non-GAAP gross "
     "margin; FY26 guided $230-260M <i>before</i> LivePerson; $203M cash, no debt; $42.8M GAAP net loss in "
     "the quarter. Inside the $2-20B Bucket 4 band. <b>38.5% of the float is short.</b>"),
    ("Evolution Metals &amp; Technologies Corp.", "EMAT", "NASDAQ", "$1.83B (FMP) / <b>$1.92B</b> (yfinance)",
     "Materials", "Rare Earth &amp; Critical Materials", "2026-06-30",
     "Russell addition (Bucket 5 &mdash; QUEUED, reply <code>add EMAT</code>)",
     "SSMR, LIN, MP",
     "Carried from the 09-04 report, which told you to <i>&ldquo;reply <code>add EMAT</code>&rdquo;</i> &mdash; "
     "but EMAT had no ledger row, so <b>that reply would have been ignored</b>. It is queued now so the "
     "instruction is live for the first time. The case has weakened: both vendors now put it <b>below</b> the "
     "$2B Bucket 5 floor (was $1.95B / $2.05B a week ago), and fiscal 2026 revenue is guided at $5-8M "
     "(Q1 $1.9M, Q2 $1.6M) against a fiscal 2027 guide of $400-460M that is entirely the Pohang magnet ramp "
     "landing before the 1 Jan 2027 DFARS deadline. $5.3M cash against $27.5M debt."),
]

SUMMARIES = [
    ("SoundHound AI, Inc. (SOUN)",
     "We build the voice that answers when you call, drive up to, or type at another company. Our agents take "
     "drive-thru and phone orders for chains including Five Guys, IHOP and Jersey Mike's; our assistant is "
     "embedded in cars from Stellantis, Hyundai/Kia and several Chinese OEMs, earning a per-unit royalty at "
     "build plus a connected-services fee for the life of the vehicle; and with LivePerson we now also run the "
     "enterprise chat and messaging layer inside large contact centres. We charge subscriptions and usage fees "
     "on the enterprise side and royalties on the automotive side. Q2 2026 revenue was $61.9M, up 45% year "
     "over year at a 58% non-GAAP gross margin, and we are still loss-making."),
    ("Evolution Metals &amp; Technologies Corp. (EMAT)",
     "We are building a rare-earth permanent-magnet supply chain that does not run through China. Our operating "
     "asset is a plant in Pohang, South Korea, which we are expanding toward roughly 10,000 metric tons a year "
     "of magnet capacity, including about 6,000 tons of high-performance sintered NdFeB, with Tier-1 OEM "
     "quality certifications across six grades. Our customers are defence primes and automotive and industrial "
     "OEMs who from 1 January 2027 face a DFARS rule barring Chinese-origin magnets from US defence hardware. "
     "We are pre-scale today &mdash; $1.9M of revenue in Q1 2026 and $1.6M in Q2 &mdash; so essentially all of "
     "the $400-460M fiscal 2027 guidance is capacity that has not yet been built or sold."),
]


def rec_rows():
    out = []
    for (co, tk, ex, cap, sec, sub, dt, trig, peers, reason) in RECS:
        out.append(
            "<tr>"
            f"<td><b>{co}</b></td><td><code>{tk}</code></td><td>{ex}</td><td align='right'>{cap}</td>"
            f"<td>{sec}</td><td>{sub}</td><td>{dt}</td><td>{trig}</td><td>{peers}</td>"
            f"<td>{reason}</td>"
            "</tr>")
    return "\n".join(out)


BODY = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222">

<h2>Weekly Coverage Universe Additions &mdash; {DATE}</h2>

<p><b>Window</b> 2026-09-04 &rarr; 2026-09-11 (7d) &middot; <b>Universe</b> 1,352 &rarr; 1,354 &middot;
<b>Action needed:</b> <code>SOUN</code> &middot; <code>EMAT</code> &middot; <code>688836.SS</code></p>

<h3>Decisions</h3>
<ul>
<li><b>Added by rule &mdash; 0.</b> Nothing priced at or above $25B, no separation above $10B closed, and no
new core-sector registrant.</li>
<li><b>Added by your request &mdash; 2.</b> <code>CMP</code> Compass Minerals and <code>SOM.AX</code> SomnoMed,
written 2026-09-07 from your <code>#project-ideas</code> message of 08-25. Not this lane's work.</li>
<li><b>Awaiting your reply &mdash; 3.</b> <code>SOUN</code> and <code>EMAT</code> are new;
<code>688836.SS</code> Unitree is in its <b>fourth</b> consecutive week.</li>
<li><b>Remove &mdash; 6 new, 4 carried.</b> New: <code>FBRX</code>, <code>LPSN</code>, <code>BRNS</code>,
<code>BTAI</code>, plus remaps <code>CYCN</code>&rarr;<code>KRSA</code> and
<code>GLMD</code>&rarr;<code>EOCN</code>. Carried: <code>APGE</code>, <code>CRNX</code>, <code>TALK</code>,
<code>BCAB</code>.</li>
<li><b>Flagged &mdash; 2.</b> <code>IOBT</code> has been a <b>Chapter 7 liquidation since 2026-03-31</b> and is
still in the universe; <code>SGMO</code> is in Chapter 11 and off Nasdaq since 2026-05-05. Both are reported
every week as ordinary &ldquo;rename&rdquo; candidates.</li>
</ul>

<h3>Recommendations</h3>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:12px">
<tr style="background:#f0f0f0">
<th>Company</th><th>Ticker</th><th>Exchange</th><th>Market Cap</th><th>Sector</th><th>Subsector</th>
<th>Listing Date</th><th>Trigger</th><th>Peers in sheet</th><th>Reason to add</th>
</tr>
{rec_rows()}
</table>

<h3>Company Summaries</h3>
{''.join(f'<p><b>{h}</b><br>{t}</p>' for h, t in SUMMARIES)}

<h3>Notes</h3>
<ul>
<li><b>Two reply instructions in last week's report were dead ends.</b> <code>poll_ipo_replies.py</code> is
gated to <code>pending</code> ledger rows. <code>add EMAT</code> had no ledger row at all;
<code>decline 0625.HK</code> named a row whose status is <code>approved</code>. Both would have been silently
ignored. The second matters most: an auto-added name is written as <code>approved</code>, which is exactly the
status the poller refuses to act on, so <b>there is currently no reply that reverses an auto-add</b> &mdash;
and reversal-by-reply is the whole justification for adding without asking. Two small changes are proposed in
the report; no code has been changed.</li>
<li><b>Dangote Refinery: a $49B IPO opens 09-14.</b> 4.1bn shares at NGN 525 on the Nigerian Exchange, $1.6bn
raise, $2.5bn already placed privately. That is a <b>mandatory Bucket 2 add when it lists</b>, at nearly twice
the bar.</li>
<li><b>Anthropic has a date.</b> Prospectus public late September, roadshow October, listing early November
(Reuters 09-04); Morgan Stanley and Goldman as leads (FT). Bucket 2 on any plausible size.</li>
<li><b>Brooks Automation confidentially submitted an S-1 on 09-10</b> &mdash; semiconductor wafer and reticle
automation plus contamination control, the THL-owned half of the original Brooks after the 2022 Azenta
separation. It is the closest core adjacency anywhere in the pipeline. No size disclosed, by design: a
confidential submission has no fee table.</li>
<li><b><code>RML</code> came within 14% of firing Bucket 2 on a vendor artifact.</b> Resolution Minerals listed
ADSs on Nasdaq 09-09. FMP publishes $21.09B and yfinance $21.91B; the company is worth about <b>US$53M</b>.
Each ADS represents 200 ordinary shares and both vendors multiply the ordinary share count by the ADS price
&mdash; a 414x overstatement that lands squarely in the band the size-gated buckets read.</li>
<li><b>Five covered names left the exchange this week; the daily lane named three.</b> The symbol-directory
step diffs day over day, so <code>BTAI</code> (Chapter 11, Nasdaq suspension 09-08) and <code>LPSN</code>
(SoundHound merger, closed 09-04) appear in no daily report a human reads &mdash; and both are adjudicated
<i>&ldquo;listed &mdash; likely a symbol-format mismatch&rdquo;</i> in this morning's table. That is verbatim
the defect diagnosed on 09-04 for <code>APGE</code> and <code>CRNX</code>, now with two more instances.</li>
<li><b><code>CYCN</code> is now Korsana Biosciences.</b> SEC CIK 1755237 reads <i>Korsana Biosciences, Inc.</i>;
<code>KRSA</code> began trading 09-09 after a 1-for-7 reverse split, with a $380M private placement, ~$475M
cash into 2029, and an anti-amyloid antibody for Alzheimer's. It did <b>not</b> auto-add under Bucket 1,
because a ticker change on a covered registrant is not a listing. Remap or remove-and-re-add &mdash; your
call; both are set out in the report.</li>
<li><b>The confidential-submission ledger gained one entry and lost two rumours.</b> Anthropic and Inspire
Brands were recorded last week as press <i>reports</i> when each company had published its own statement
months earlier. Both are now <code>announcement</code>. The field exists to stop a rumour inheriting a fact's
authority; here it failed in the other direction and two facts spent a week labelled UNCONFIRMED.</li>
<li><b>Schwab has produced no new-issue alerts for six months.</b> The search is run every week and has never
returned anything. The subscription is probably off.</li>
</ul>

<h3>CSV Changes</h3>
<p>This lane added and removed nothing. Two rows entered the universe inside the window on your instruction:
<code>CMP</code> (Compass Minerals International, Materials, NYSE) and <code>SOM.AX</code> (SomnoMed,
MedTech / Sleep, ASX), written 2026-09-07. Row count <b>1,352 &rarr; 1,354</b>. Ten rows are removal or remap
candidates awaiting your decision; a removal is never made without approval.</p>

<h3>Report Files Generated</h3>
<ul>
<li><code>weekly_coverage_universe_additions_{DATE}.md</code> &mdash; this week's recommendations report</li>
<li><code>company_backgrounds_{DATE}.md</code> &mdash; full background briefings for SOUN and EMAT</li>
<li><code>data/discovery_output_{DATE}.json</code> &mdash; structured candidate output, schema-validated</li>
<li><code>data/confidential_watch.json</code> &mdash; confidential-submission ledger; one added, two reclassified</li>
<li><code>form10_watch_{DATE}.md</code> &mdash; Form 10-12B spin-off and uplisting watch</li>
<li><code>s1_watch_{DATE}.md</code> &mdash; S-1 / F-1 IPO pipeline watch</li>
<li><code>symbol_directory_{DATE}.md</code> &mdash; US exchange symbol-directory diff and covered-name cross-check</li>
<li><code>delisted_check_{DATE}.md</code> / <code>.csv</code> &mdash; yfinance identity and price-recency probe</li>
<li><code>ticker_change_check_{DATE}.md</code> / <code>.csv</code> &mdash; SEC CIK-keyed ticker-change check</li>
<li><code>foreign_crosscheck_{DATE}.md</code> &mdash; foreign metadata cross-check vs iShares and SEC N-PORT</li>
<li><code>isin_identity_{DATE}.md</code> &mdash; ISIN to issuer-name identity audit via OpenFIGI</li>
<li><code>cik_name_resolution_{DATE}.md</code> &mdash; CIK to SEC-title resolution report</li>
</ul>

<p>Full report on the published page:
<a href="https://jroypeterson.github.io/Coverage-Manager/">jroypeterson.github.io/Coverage-Manager</a><br>
Reports folder:
<a href="https://www.dropbox.com/home/Claude%20Folder/Coverage%20Manager/reports">Dropbox &rsaquo; Coverage Manager &rsaquo; reports</a></p>

<p>&mdash; Coverage Universe Builder</p>
</body></html>"""


def main():
    msg = MIMEText(BODY, "html", "utf-8")
    msg["Subject"] = SUBJECT
    msg["From"] = ADDR
    msg["To"] = "jroypeterson@gmail.com"
    msg["Date"] = formatdate(localtime=True)

    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(ADDR, PW)
    m.append('"[Gmail]/Drafts"', "\\Draft",
             imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    m.logout()
    print(f"draft appended: {SUBJECT}")


if __name__ == "__main__":
    main()
