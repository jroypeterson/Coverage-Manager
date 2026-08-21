"""Build the 2026-08-21 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft_0814.py. Safe to delete after the run.

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
DATE = "2026-08-21"
SUBJECT = f"[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — {DATE}"

RECS = [
    ("688836.SS", "Unitree Robotics Technology Co., Ltd.", "Shanghai STAR", "~$40.5B",
     "Tech", "Industrial Robotics / Automation", "2026-08-19",
     "IPO (Bucket 2 — mandatory)", "2715.HK, 688825.SS, TSLA, ISRG",
     "Any IPO globally at or above $25B is a mandatory add regardless of sector. Unitree is "
     "the first pure-play humanoid-robot maker to get a public-market price. It priced at "
     "$9.1B on 08-06 and closed its 08-19 debut up 460%, so the bucket fires on MARKET CAP, "
     "not on the offer — that is the one thing to check before keeping it. The write was "
     "REFUSED by the enrichment gate: neither yfinance nor OpenFIGI has a company name for a "
     "two-day-old A-share, so the row is pending, not added."),
]

PIPELINE = [
    ("Shein", "(HKEX)", "IPO", "$26-27B, ~$2B raise", "book 08-19, trades ~08-28",
     "THE NUMBER CROSSED THE BAR. $30-40B on 08-04, $22-25B on 08-10, now $26-27B (Bloomberg "
     "08-17) after investor pushback. Above $25B it is a mandatory Bucket 2 add the moment it "
     "prices. Q1 2026 was a $99M loss against a $395M profit a year earlier."),
    ("Anthropic", "—", "IPO", "raise to match or top SpaceX's $86.2B; ~$2tn talk",
     "public filing as early as end-Aug; listing Oct",
     "MATERIALLY ADVANCED THIS WEEK. Confidential S-1 already with the SEC (Bloomberg 08-20); "
     "Morgan Stanley, Goldman and JPMorgan mandated. Would be the largest listing ever."),
    ("OpenAI", "—", "IPO", "undisclosed", "2027",
     "New this week. Renaissance is 'not counting out a Q4 IPO' either."),
    ("AgiBot", "(HKEX)", "IPO", "undisclosed; US$593M expected 2026 revenue", "not set",
     "Surfaced by the Unitree work. Unitree's largest domestic competitor, on more forward "
     "revenue than Unitree has guided to. Together ~80% of Chinese humanoid shipments in 2026. "
     "Not filed — 'chasing' a listing."),
    ("Advasa Holdings", "ADBT", "IPO", "94.0M shares, no terms", "08-18, slipped",
     "Still 'expected' on the Finnhub calendar with no published terms."),
    ("MetaOptics", "MOT", "IPO", "3.0M shares at $5-7, ~$24M", "08-14, slipped",
     "Singapore metalens maker; adjacent to the optical complex. $1M FY2025 revenue."),
    ("Switch", "—", "IPO", "undisclosed", "Nov 2026", "Confidentially filed 08-10. Data centres."),
    ("Formlabs", "—", "IPO", "up to $500M raise", "not set", "3D printers. Adjacent instrumentation."),
    ("CyrusOne", "—", "IPO", "undisclosed", "2027", "KKR/BlackRock data-centre operator."),
    ("Lancium", "—", "IPO", "undisclosed", "2027", "Data centre; NVDA investing $2-3B."),
]

SUMMARIES = [
    ("Unitree Robotics Technology Co., Ltd. (688836.SS)",
     "We design and build legged robots and the motors, reducers and controllers inside them. "
     "We sell quadrupeds — the Go2, B2 and A2 — and humanoids — the G1, H1, R1 and H2 — mostly "
     "to universities, research labs, industrial inspection contractors and, increasingly, "
     "entertainment and retail buyers, at prices from a few thousand dollars for a consumer "
     "quadruped to roughly RMB 166,000 for a humanoid. We make money on hardware units, and we "
     "protect the margin by manufacturing our own actuators and joint modules rather than "
     "buying them, which is why gross margin ran 60.4% in FY2025 against a mid-40s level two "
     "years earlier. FY2025 revenue was RMB 1.70B, up 335%, with RMB 278M of attributable net "
     "profit — we are one of the very few humanoid-robot companies that earns anything at all."),
]

FILES = [
    ("weekly_coverage_universe_additions_2026-08-21.md",
     "This week's recommendations report: the Bucket 2 refusal, pipeline, lane findings, exclusions"),
    ("company_backgrounds_2026-08-21.md",
     "Full investment briefing for Unitree Robotics"),
    ("data/discovery_output_2026-08-21.json",
     "Structured candidate record consumed by the candidate ledger (1 candidate)"),
    ("form10_watch_2026-08-21.md",
     "Form 10-12B spin-off and uplisting registrations (8 registrants, 0 new, 0 inconclusive)"),
    ("symbol_directory_2026-08-21.md",
     "Nasdaq/NYSE symbol-directory diff and universe cross-check (TALK removed from the exchange)"),
    ("delisted_check_2026-08-21.md / .csv",
     "Delisting and recycled-ticker probe across the universe (2 flagged: APM, VYNE)"),
    ("foreign_crosscheck_2026-08-21.md",
     "Foreign metadata cross-check against SEC N-PORT filings"),
    ("isin_identity_2026-08-21.md",
     "ISIN-to-issuer identity audit via OpenFIGI"),
    ("cik_name_resolution_2026-08-21.md",
     "CIK name resolution review"),
]

TH = ("padding:6px 9px;border:1px solid #d0d7de;background:#f2f5f8;text-align:left;"
      "font-size:12px;font-weight:600;")
TD = "padding:6px 9px;border:1px solid #d0d7de;font-size:12px;vertical-align:top;"


def _table(head, rows):
    body = "".join(
        "<tr>" + "".join(f'<td style="{TD}">{c}</td>' for c in r) + "</tr>" for r in rows)
    return ('<table style="border-collapse:collapse;width:100%">'
            "<tr>" + "".join(f'<th style="{TH}">{h}</th>' for h in head) + "</tr>"
            + body + "</table>")


def rec_table():
    return _table(("Ticker", "Company", "Exchange", "Market Cap", "Sector", "Subsector",
                   "Listing Date", "Trigger", "Peers in sheet", "Reason to add"), RECS)


def pipeline_table():
    return _table(("Company", "Ticker", "Event", "Size / valuation", "Expected", "Note"), PIPELINE)


BODY = f"""<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
color:#1b1f24;line-height:1.5;max-width:1100px">

<h2 style="margin-bottom:4px">Weekly Coverage Universe Additions &mdash; {DATE}</h2>
<p style="color:#57606a;margin-top:0;font-size:13px">
Review window 2026-08-14 &rarr; 2026-08-21 (7 days) &middot;
universe 1,349 &rarr; 1,349 rows &middot;
<b>1 pending your approval</b> &middot; <b>0 added by rule</b> &middot;
<b>action needed: TALK</b>
</p>

<p><b>One covered name stopped trading and one mandatory add did not land.</b> Those are the two
things in this report.</p>

<p><b>TALK (Talkspace) left Nasdaq on 2026-08-17.</b> Universal Health Services closed its
acquisition at $5.25 per share in cash, $870.6M in total; Talkspace has filed a Form 25 to delist.
The row is <code>Healthcare Services / HIT</code> and <b>Core = Y</b>. It should come out of the
universe and go into <code>data/delisted_tickers.csv</code> &mdash; I have not touched either file,
because a removal is your call. Worth noting that <b>UHS is also covered and also Core = Y</b>, so
this is a read-through into a name you already model, not merely an exit from one you no longer
can.</p>

<p><b>688836.SS (Unitree Robotics) fired Bucket 2 at ~$40.5B and the write was refused.</b> The
enrichment gate could not resolve a company name from yfinance or OpenFIGI for a two-day-old
Shanghai A-share, so the row stayed <code>pending</code> rather than entering the universe
half-filled. That is the gate working as designed. It is also a timing gap that does not self-heal:
the weekly sync only auto-adds this week's candidates, so I will re-propose it next week when
vendor metadata has propagated.</p>

<p><b>The number to check on Unitree is the one the rule does not specify.</b> It priced on 08-06
at RMB 150.8, a <b>$9.1B</b> valuation &mdash; below the bar. It closed its 08-19 debut up 460% and
is at <b>$40.5B</b> today. Bucket 2 says <i>market cap</i>, not <i>offer valuation</i>, so it fires
&mdash; but the whole $31B of difference is two days of an 8,000x-oversubscribed retail book, and
the trailing P/E is roughly 978x. If you read Bucket 2 as an offer-price test,
<code>decline 688836.SS</code> is the right answer.</p>

<p><b>The forward book advanced on both mandatory triggers.</b> Shein now targets
<b>$26&ndash;27B</b> (Bloomberg, 08-17), back above the $25B bar it fell below a week ago, with the
book opened 08-19 and trading expected around 08-28. Anthropic has a <b>confidential S-1 already
with the SEC</b> and could file publicly by month end, sizing a raise to match or top SpaceX's
record $86.2B.</p>

<h3 style="margin-top:26px">Recommendations</h3>
<p style="font-size:13px;color:#57606a;margin-top:0">One name, and it is a mandatory Bucket 2 add
the write path refused &mdash; not a judgement call. The only judgement left is whether you want it
at all. Reply <code>decline 688836.SS</code> to drop it.</p>
{rec_table()}

<h3 style="margin-top:26px">Pending approval backlog</h3>
<p style="font-size:13px"><b>1 name pending</b>, and it is this week's:
<code>688836.SS</code> (Unitree Robotics), proposed 2026-08-21, Bucket 2, enrichment refused and
queued for retry next week. Nothing expired this run. The ledger holds 39 rows &mdash; 1 pending,
the rest decided.</p>

<h3 style="margin-top:26px">Pipeline / filings to monitor</h3>
{pipeline_table()}

<p style="font-size:13px"><b>Form 10 book:</b> 8 registrants, 0 new, <b>0 inconclusive</b> &mdash;
every registrant had a readable SIC, so there is no bucket this week where a missed listing could
hide. FedEx Freight and Honeywell Aerospace remain the two Bucket 3 candidates on parent size;
First Tracks Biotherapeutics (AnaptysBio) and Atrium Therapeutics are Bucket 1 core-sector at any
size. Sizes are unknown by construction until separation.</p>

<p style="font-size:13px"><b>Russell:</b> no additions this week and none expected. FTSE Russell
adds eligible IPOs quarterly in March, June, September and December; the next window is
<b>September 2026</b>. A Gmail sweep on <code>subject:Russell</code> for the last 10 days returned
only StreetAccount daily index recaps. This is a calendar fact, not a quiet search.</p>

<h3 style="margin-top:26px">Listing-lane findings</h3>
<p style="font-size:13px"><b>Covered names removed from the exchange this period: TALK.</b> The
symbol-directory diff (7,497 rows) showed 12 new listings and 17 removals against the 08-17
snapshot.</p>
<p style="font-size:13px"><b>delisted_check did not catch TALK, and structurally could not.</b> It
traded until 08-17, so its last bar is four days old against a PRICE_STALE_DAYS of 10; it appears
in that report instead as a <i>symbol mismatch</i>, because SEC still lists TALK while the Form 25
runs its ten days. The exchange-directory lane saw it the day after it happened &mdash; it reads
the exchange's own record rather than waiting for a price feed to go stale.</p>
<p style="font-size:13px"><b>Completeness check on my own IPO sweep: no misses, and two traps.</b>
<code>VMRK</code> is <b>not a new listing</b> &mdash; it is the AvalonBay / Equity Residential
merger of equals, closed 08-17, renamed Vivmark Residential at ~$51B equity cap, and it pairs with
EQR in the removed column. A $51B symbol appearing on the NYSE that is not a listing event would
have been a false positive of exactly the size Bucket 2 exists to catch. <code>IA</code> is
likewise a rename of <code>ISSC</code> (Innovative Solutions and Support &rarr; Innovative
Aerosystems). The rest of the diff is SPACs, preferred and depositary lines, and three genuine
nano-caps.</p>
<p style="font-size:13px"><b>Nasdaq financial-status flags:</b> 78 covered names carry one (76
deficient, AVXL delinquent, CELU both). Week over week: 2 new (<b>CIRC</b>, <b>ELUT</b>, both
deficient), 2 cleared (LEXX, RNXT), no code changes &mdash; all four in the small-cap biotech tail.
<b>15 covered US rows remain unadjudicable for want of a CIK</b> (AFMD, APLT, AVDL, CLSD, CVAC,
CYBR, CYTO, DVAX, EVOK, GBIO, MNK, MNMD, MRSN, RPTX, SNCR); left inconclusive deliberately rather
than folded into "delisted".</p>

<h3 style="margin-top:26px">Considered and excluded</h3>
<ul style="font-size:13px">
<li><b>Lyntris (LYNX)</b> &mdash; $1.79B, IPO 08-19. The 08-14 report said this would be a
"Bucket 4 judgement if it prices". The judgement is no, and pricing is why: it cut to 17M shares
and priced at <b>$17.50 against a $19&ndash;22 range</b>, opened at $15.50 and fell 11.4% on debut.
At $2.01B on the offer it grazed the Bucket 4 floor; at $1.79B today it is below it. Defense-tech
roll-up, $451M LTM revenue, not a core sector. Revisit if it re-rates over $2B.</li>
<li><b>Vivmark Residential (VMRK)</b> $51B and <b>Innovative Aerosystems (IA)</b> $375M &mdash;
not listing events; a merger rename and a ticker rename respectively.</li>
<li><b>First Breach (FBDT)</b> $187M, <b>Brightline Interactive (BTLN)</b> ~$20M,
<b>Tactical Resources (TREO)</b> $98M, <b>QNB Corp (QNBC)</b> $178M &mdash; nano- and micro-caps
in aerospace, VR, mining and community banking. No relevance, orders of magnitude below any
bucket floor.</li>
<li><b>MetaOptics (MOT)</b> &mdash; carried from 08-14 and unchanged: genuinely adjacent to the
optical complex, but $1M of FY2025 revenue. Pre-commercial; revisit if it scales.</li>
<li><b>SPACs and non-operating vehicles</b> &mdash; NSAIU, XTERU, TBCVU, LEDRU, plus the Charter
and Santander preferred lines and the MUA.R rights. Excluded by standing policy.</li>
</ul>

<h3 style="margin-top:26px">Company Summaries</h3>
{''.join(f'<p style="margin-bottom:14px"><b>{t}</b><br>{s}</p>' for t, s in SUMMARIES)}

<h3 style="margin-top:26px">Notes</h3>
<ul style="font-size:13px">
<li><b>One thing needs a reply: TALK.</b> Removing a row is your call and I have not made it.
The evidence is not in doubt &mdash; merger closed 08-17, Form 25 filed, $5.25 cash &mdash; only
the authorisation is.</li>
<li><b>Unitree's market cap is computed, not quoted.</b> No vendor serves a cap for 688836.SS yet.
I derived 404.5M post-IPO shares from the offer economics (RMB 6.1B / RMB 150.80 = 40.45M new
shares; RMB 61B / 150.80 = 404.5M total) and multiplied by the live RMB 672.41 at a CNY/USD of
0.14904. It cross-checks against the published "+629% intraday = $66B" figure, which reproduces
exactly on the same share count.</li>
<li><b>Two forward triggers are worth a diary entry:</b> Shein trades around 08-28 and its final
price decides mandatory-add versus judgement call &mdash; it is currently on the mandatory side of
the line. Anthropic could file publicly within days.</li>
<li>Sources: Finnhub IPO calendar (18 events, 08-11 &rarr; 09-05), Renaissance Capital's 08-16 US
IPO recap, StreetAccount macro headlines through 08-21, both listing lanes, and web validation on
every named candidate. Market caps for LYNX, FBDT, TREO, IA and VMRK were verified against live
quotes.</li>
</ul>

<h3 style="margin-top:26px">CSV Changes</h3>
<p style="font-size:13px"><b>No changes to the coverage universe CSV this week.</b>
<code>data/coverage_universe_tickers.csv</code> stands at <b>1,349 rows</b>, unchanged from
2026-08-14. Two changes are queued rather than made: the <code>688836.SS</code> add that the
enrichment gate refused, and the <code>TALK</code> removal awaiting your authorisation.</p>

<h3 style="margin-top:26px">Report Files Generated</h3>
<ul style="font-size:13px">
{''.join(f'<li><code>{n}</code> &mdash; {d}</li>' for n, d in FILES)}
</ul>
<p style="font-size:13px">Reports folder:
<a href="https://www.dropbox.com/home/Claude%20Folder/Coverage%20Manager/reports">Dropbox</a>
&middot; Published page:
<a href="https://jroypeterson.github.io/Coverage-Manager/">jroypeterson.github.io/Coverage-Manager</a></p>

<p style="margin-top:26px;font-size:13px;color:#57606a">
Reply in the #ipo-spinoffs-newissues thread to act:
<code>add TICKER</code> &middot; <code>decline TICKER</code> &middot; <code>add all</code>.
Replies are applied automatically by CoverageManager-IpoReplyPoll (09:20 / 13:20 / 18:20 daily).
</p>

<p style="margin-top:20px">Signed,<br><b>Coverage Universe Builder</b></p>
</body></html>"""


def main():
    msg = MIMEText(BODY, "html", "utf-8")
    msg["Subject"] = SUBJECT
    msg["From"] = ADDR
    msg["To"] = "jroypeterson@gmail.com"
    msg["Date"] = formatdate(localtime=True)

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(ADDR, PW)
    folder = '"[Gmail]/Drafts"'
    rc, _ = M.append(folder, "\\Draft", imaplib.Time2Internaldate(time.time()),
                     msg.as_bytes())
    print("append:", rc)
    M.logout()
    print("Draft created:", SUBJECT)


if __name__ == "__main__":
    main()
