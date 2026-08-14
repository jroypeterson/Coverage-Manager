"""Build the 2026-08-14 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft.py. Safe to delete after the run.
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
DATE = "2026-08-14"
SUBJECT = f"[Agentic Investing] — Weekly Coverage Universe Additions — {DATE}"

AUTO = [
    ("VOGX", "Vogenx, Inc.", "Nasdaq", "~$196M", "Biopharma", "Biotech", "2026-08-12",
     "IPO (Bucket 1 auto-add)", "NVO, LLY, VKTX, CRNX",
     "Core-sector listing, any size. Priced 6.25M sh at $13.00 for $81.3M gross, top of range. "
     "Mizagliflozin is a gut-restricted SGLT1 inhibitor for post-bariatric hypoglycemia — no "
     "approved therapy exists. Gastroparesis (2027) points at the best-known GLP-1 side effect, "
     "so the asset gets more relevant as NVO/LLY volumes grow."),
    ("BSEM", "BioStem Technologies, Inc.", "Nasdaq", "~$55M", "Biopharma", "Biotech", "2026-08-07",
     "Uplisting / Direct listing (Bucket 1 auto-add)", "MDXG, ORGO, IART",
     "Core-sector listing, any size. OTC-to-Nasdaq uplisting off the Form 10-12B our own lane "
     "flagged on 08-04 — no offering, so no IPO calendar saw it. CAUTION: FY2024 revenue was "
     "restated $301.8M -> $69.7M; FY2025 $47.5M; FY2026 guided $26-29M; $7.0M cash against $5.5M "
     "quarterly burn. Kept for the MDXG/ORGO CMS read-through. Reply 'decline BSEM' to reverse."),
]

PIPELINE = [
    ("Lyntris", "LYNX", "IPO", "$492M raise / $2.53B valuation", "week of 08-17",
     "Terms set 08-10: 24M sh at $19-22, 80% secondary. CORRECTS OUR OWN ESTIMATE — 07-31 said "
     "'~$100M', 08-07 said 'microcap'. Defense-tech roll-up, $451M LTM revenue. Not core sector."),
    ("Shein", "(HKEX)", "IPO", "$22-25B (Bloomberg Intelligence, 08-10)", "mid-Aug, may slip",
     "THE NUMBER MOVED THE WRONG WAY. Pitched at $30-40B on 08-03; now below $30B and BI at "
     "$22-25B. At $25B+ mandatory Bucket 2; below, a Bucket 4 judgement call."),
    ("Anthropic", "—", "IPO", "~$2tn talk (FT, 08-13)", "Oct 2026 (PitchBook)",
     "Would be the largest listing in history and an automatic Bucket 2 add many times over."),
    ("Switch", "—", "IPO", "undisclosed", "Nov 2026", "Confidentially filed 08-10. Data centres."),
    ("Formlabs", "—", "IPO", "up to $500M raise", "not set", "3D printers. Adjacent instrumentation."),
    ("Advasa Holdings", "ADBT", "IPO", "terms not set", "08-17", "On the Finnhub calendar; no terms."),
    ("CyrusOne", "—", "IPO", "undisclosed", "2027", "KKR/BlackRock data-centre operator (Reuters)."),
    ("Lancium", "—", "IPO", "undisclosed", "2027", "Data centre; NVDA investing $2-3B."),
]

SUMMARIES = [
    ("Vogenx, Inc. (VOGX)",
     "We are a clinical-stage biopharmaceutical company developing mizagliflozin, an oral, "
     "minimally-absorbed selective inhibitor of SGLT1 — the transporter that pulls glucose out "
     "of the small intestine. By blocking glucose absorption locally in the gut rather than "
     "systemically in the kidney, we blunt the post-meal glucose-and-insulin spike that causes "
     "post-bariatric hypoglycemia, a debilitating condition in patients who have had gastric "
     "bypass and for which no drug is approved. We have dosed more than 500 subjects across 10 "
     "clinical studies and are advancing the same molecule into gastroparesis and GIP-dependent "
     "Cushing's syndrome. We have no product revenue; our IPO proceeds of roughly $81 million "
     "fund a Phase 2b in post-bariatric hypoglycemia and a 2027 Phase 2 start in gastroparesis, "
     "into approximately 2028."),
    ("BioStem Technologies, Inc. (BSEM)",
     "We process donated human placental tissue into allografts — thin, sheet-form biologic "
     "dressings sold under the Vendaje, Vendaje AC and Vendaje Optic brands — using our "
     "proprietary BioREtain method, which is designed to preserve the tissue's native growth "
     "factors rather than devitalise them. We sell to wound-care clinics, hospital outpatient "
     "departments and physician offices treating chronic diabetic foot ulcers and venous leg "
     "ulcers, and we are reimbursed almost entirely by Medicare under product-specific Q-codes, "
     "which makes CMS pricing policy the single largest determinant of our revenue. We generated "
     "$7.9 million of net revenue in Q2 2026, up 29% sequentially, and guide to $26-29 million "
     "for the full year. We uplisted from OTC to the Nasdaq Capital Market on 7 August 2026."),
]

FILES = [
    ("weekly_coverage_universe_additions_2026-08-14.md",
     "This week's recommendations report: auto-adds, pipeline, lane findings, exclusions"),
    ("company_backgrounds_2026-08-14.md",
     "Full investment briefings for VOGX and BSEM"),
    ("data/discovery_output_2026-08-14.json",
     "Structured candidate record consumed by the candidate ledger"),
    ("form10_watch_2026-08-14.md",
     "Form 10-12B spin-off and uplisting registrations (7 registrants, 0 new)"),
    ("symbol_directory_2026-08-14.md",
     "Nasdaq/NYSE symbol-directory diff and universe cross-check (0 covered names removed)"),
    ("delisted_check_2026-08-14.md / .csv",
     "Delisting and recycled-ticker probe across the universe"),
    ("ticker_change_check_2026-08-14.md / .csv",
     "SEC ticker-change and deregistration review"),
    ("foreign_crosscheck_2026-08-14.md",
     "Foreign metadata cross-check against SEC N-PORT filings"),
    ("isin_identity_2026-08-14.md",
     "ISIN-to-issuer identity audit via OpenFIGI"),
    ("cik_name_resolution_2026-08-14.md",
     "CIK name resolution review"),
]

TH = ("padding:6px 9px;border:1px solid #d0d7de;background:#f2f5f8;text-align:left;"
      "font-size:12px;font-weight:600;")
TD = "padding:6px 9px;border:1px solid #d0d7de;font-size:12px;vertical-align:top;"


def auto_table():
    head = ("Ticker", "Company", "Exchange", "Market Cap", "Sector", "Subsector",
            "Listing Date", "Trigger", "Peers in sheet", "Reason to add")
    rows = "".join(
        "<tr>" + "".join(f'<td style="{TD}">{c}</td>' for c in r) + "</tr>" for r in AUTO)
    return ('<table style="border-collapse:collapse;width:100%">'
            "<tr>" + "".join(f'<th style="{TH}">{h}</th>' for h in head) + "</tr>"
            + rows + "</table>")


def pipeline_table():
    head = ("Company", "Ticker", "Event", "Size / valuation", "Expected", "Note")
    rows = "".join(
        "<tr>" + "".join(f'<td style="{TD}">{c}</td>' for c in r) + "</tr>" for r in PIPELINE)
    return ('<table style="border-collapse:collapse;width:100%">'
            "<tr>" + "".join(f'<th style="{TH}">{h}</th>' for h in head) + "</tr>"
            + rows + "</table>")


BODY = f"""<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
color:#1b1f24;line-height:1.5;max-width:1100px">

<h2 style="margin-bottom:4px">Weekly Coverage Universe Additions &mdash; {DATE}</h2>
<p style="color:#57606a;margin-top:0;font-size:13px">
Review window 2026-08-04 &rarr; 2026-08-14 (10 days) &middot;
universe 1,347 &rarr; 1,349 rows &middot;
<b>0 pending your approval</b> &middot; <b>2 added by rule</b>
</p>

<p>A quiet week for decisions and a loud one for the pipeline. Two core-sector listings happened
and <b>both were added automatically under Bucket 1</b> &mdash; Vogenx priced on 08-12, and
BioStem completed the OTC-to-Nasdaq uplisting our Form 10 lane flagged eight days ago. Neither
needs a reply; both are reversible with one word.</p>

<p><b>BioStem carries a caveat I am putting in front of you rather than in a footnote:</b> its
FY2024 revenue was restated from $301.8M to $69.7M, FY2025 came in at $47.5M, FY2026 is guided to
$26&ndash;29M, and it holds $7.0M of cash against $5.5M of quarterly burn with no raise attached
to the uplisting. The rule added it; you should know exactly what the rule added. Reply
<code>decline BSEM</code> if you disagree.</p>

<p>The pending backlog is <b>empty</b> for the first time since the ledger was built.</p>

<p>The forward book is where the value is. <b>Lyntris prices the week of 08-17 at a $2.53B
valuation</b> &mdash; our 07-31 report estimated that deal at "~$100M" and 08-07 called it
"microcap"; both were wrong by a factor of twenty-five. <b>Shein</b> remains the mandatory
Bucket 2 trigger to watch, but the number moved the wrong way: Bloomberg Intelligence now pegs it
at $22&ndash;25B, below the $25B bar it cleared comfortably a fortnight ago.</p>

<h3 style="margin-top:26px">Added without asking &mdash; already in the universe</h3>
<p style="font-size:13px;color:#57606a;margin-top:0">Bucket 1 auto-adds a listing in a core sector
(Biopharma, MedTech, Healthcare Services, Life Science Tools) at <b>any size, no floor</b>, per
your ruling of 2026-08-09. <b>Both names are already in the coverage universe &mdash; do not reply
<code>add</code> for them.</b> Reply <code>decline VOGX</code> or <code>decline BSEM</code> to
reverse.</p>
{auto_table()}

<h3 style="margin-top:26px">Recommendations</h3>
<p><b>None this week.</b> Both core-sector listings auto-added under Bucket 1; nothing in the
window met Bucket 2, 3, 4 or 5, and I am not manufacturing a coverage-gap proposal to fill the
section. The two most recent gap sweeps &mdash; memory on 07-31 (MU) and optical interconnect on
08-07 (FN plus the seven-name complex you approved on 08-08) &mdash; have been worked.</p>

<h3 style="margin-top:26px">Pending approval backlog</h3>
<p><b>Empty &mdash; 0 names pending.</b> You decided 15 names on 08-08 and 08-09; 34 of 36
historical candidates are now approved and 2 declined. This week's two candidates auto-added
rather than queueing, so nothing is waiting on you.</p>

<h3 style="margin-top:26px">Pipeline / filings to monitor</h3>
{pipeline_table()}

<p style="font-size:13px"><b>Form 10 book:</b> 7 registrants, 0 new, 0 inconclusive. FedEx Freight
and Honeywell Aerospace remain the two Bucket 3 candidates on parent size; First Tracks
Biotherapeutics (AnaptysBio) and Atrium Therapeutics are Bucket 1 core-sector at any size. Sizes
are unknown by construction until separation. <b>BSEM was the seventh and has now listed</b>
&mdash; filed 08-04, flagged by us 08-04, listed 08-07, added 08-14.</p>

<p style="font-size:13px"><b>Russell:</b> no additions this week and none expected. FTSE Russell
adds eligible IPOs quarterly in March, June, September and December; the next window is
<b>September 2026</b>. This is a calendar fact, not a quiet search.</p>

<h3 style="margin-top:26px">Listing-lane findings</h3>
<p style="font-size:13px"><b>Covered names removed from the exchange this period: ZERO.</b> The
symbol-directory diff (7,497 rows) showed 8 new listings and 8 removals, and <b>none of the
removals touched the coverage universe</b>.</p>
<p style="font-size:13px"><b>Completeness check on my own IPO sweep: no misses.</b> Of the 8 new
listings, 5 are SPAC or warrant lines, 1 is a closed-end fund (RVII), 1 is a rights issue, and
<code>BRNX</code> is <b>a rename of Brenmiller Energy (BNRG), not a new listing</b> &mdash; it
pairs with BNRG in the removed column and would have been a false positive.</p>
<p style="font-size:13px"><b>Nasdaq financial-status flags:</b> 80 covered names carry one (78
deficient, AVXL delinquent, CELU both) &mdash; overwhelmingly the small-cap biotech tail, no new
flag outside it. <b>15 covered US rows remain unadjudicable for want of a CIK</b> (AFMD, APLT,
AVDL, CLSD, CVAC, CYBR, CYTO, DVAX, EVOK, GBIO, MNK, MNMD, MRSN, RPTX, SNCR); left inconclusive
deliberately rather than folded into "delisted".</p>

<h3 style="margin-top:26px">Considered and excluded</h3>
<ul style="font-size:13px">
<li><b>Londian Wason New Energy Tech (FOIL)</b> &mdash; $1.89B, IPO 08-12. Chinese battery-foil
manufacturer; not a core sector and below the $2B Bucket 4 floor.</li>
<li><b>MetaOptics (MOT)</b> &mdash; ~$139M. Singapore metalens maker, genuinely adjacent to the
optical complex you just approved, which is why it is named rather than dropped. But $1M of FY2025
revenue and the deal was cut 25%. Pre-commercial; revisit if it scales.</li>
<li><b>Ticketplus (TP)</b> $88M, <b>SunScout (SNSC)</b> ~$15M &mdash; nano-caps, no relevance.</li>
<li><b>BrenX (BRNX)</b> &mdash; not a listing event; rename of Brenmiller Energy.</li>
<li><b>Robinhood Ventures Fund II (RVII)</b> &mdash; closed-end fund, not an operating company.</li>
<li><b>11 SPACs</b> &mdash; GHXI, THEO, TBCVU, PNAQU, OCLTU, NSAIU, LEDRU, TCGX, FJDIU, GVACU,
IPHXU and others. Excluded by standing policy.</li>
</ul>

<h3 style="margin-top:26px">Company Summaries</h3>
{''.join(f'<p style="margin-bottom:14px"><b>{t}</b><br>{s}</p>' for t, s in SUMMARIES)}

<h3 style="margin-top:26px">Notes</h3>
<ul style="font-size:13px">
<li><b>Nothing needs a reply from you this week.</b> Both adds were automatic and the approval
queue is empty. The only decisions available are reversals.</li>
<li><b>Two forward triggers are worth a diary entry:</b> Lyntris prices the week of 08-17 at
$2.53B; Shein's final price decides mandatory-add versus judgement call, and it has been drifting
toward the wrong side of the $25B line all month.</li>
<li><b>A data-quality observation, not a change I made.</b> The enricher set BSEM's
<code>Year Listed</code> to 2015 from FMP's ipoDate &mdash; when it began trading OTC, not when the
security now on Nasdaq began trading there. Unlike the SNDK case, BSEM kept its CIK and its
security through the uplisting, so this is genuinely ambiguous rather than plainly wrong. Flagged
rather than edited &mdash; say the word if you want uplistings to restamp Year Listed.</li>
<li>Sources: Finnhub IPO calendar (37 events, 08-01 &rarr; 09-15), the StreetAccount US Pre-IPO
Weekly Update of 08-10, both listing lanes, and web validation on every named candidate. Market
caps for VOGX, BSEM and FOIL were verified against live quotes, not vendor snapshots.</li>
</ul>

<h3 style="margin-top:26px">CSV Changes</h3>
<p style="font-size:13px"><b>2 additions, 0 removals.</b>
<code>data/coverage_universe_tickers.csv</code> went <b>1,347 &rarr; 1,349 rows</b>.
<code>VOGX</code> (Vogenx, Inc.) and <code>BSEM</code> (BioStem Technologies, Inc.) were both added
by the Bucket 1 auto-add rule and both enriched cleanly on write (CIK, ISIN, exchange, currency).
Both are classified <code>Biopharma / Biotech</code>, matching MDXG's classification for BSEM's
product category.</p>

<h3 style="margin-top:26px">Report Files Generated</h3>
<ul style="font-size:13px">
{''.join(f'<li><code>{n}</code> &mdash; {d}</li>' for n, d in FILES)}
</ul>
<p style="font-size:13px">Reports folder:
<a href="https://www.dropbox.com/home/Claude%20Folder/Coverage%20Manager/reports">Dropbox</a></p>

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
