"""Build the 2026-09-25 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft_0911.py. Safe to delete after the run.

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
DATE = "2026-09-25"
SUBJECT = f"[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — {DATE}"

# Company, Ticker, Exchange, Mkt Cap, Sector, Subsector, Listing date, Trigger, Peers, Reason
RECS = [
    ("Electra Therapeutics, Inc.", "ETRA", "NASDAQ", "$0.74B (at $11.82) / $0.94B at offer",
     "Biopharma", "Biotech", "2026-09-18",
     "IPO (Bucket 1 &mdash; <b>ADDED BY RULE</b>, already in the universe)",
     "SOBI.ST, ARGX, DNLI",
     "Upsized 23.3M shares at $15.00 for $350M, one of 2026's largest biotech IPOs. Lead antibody ipsoprubart "
     "(pan-SIRP depleter) is in Phase 2/3 for secondary HLH, a cytokine storm with no approved therapy; "
     "nearest comparator is Sobi's Gamifant. Broke issue on day one (-12%)."),
    ("ADARx Pharmaceuticals, Inc.", "ADRX", "NASDAQ", "$1.74B at the $17.00 offer",
     "Biopharma", "Biotech", "2026-09-25",
     "IPO (Bucket 1 &mdash; qualified by rule; <b>row refused</b>, still pending)",
     "ALNY, ARWR, IONS, PHVS, ABBV",
     "Upsized 26.25M shares at $17.00 (top of range) for $446.3M; AbbVie took ~4.9% in a concurrent placement. "
     "First US RNAi IPO in a decade; onvuzosiran in Phase 3 for HAE (topline end-2027). First trade was 09-25, so "
     "no vendor carried its currency yet and enrichment refused a half-filled row. Reply <code>add ADRX</code> "
     "from Monday if it is not in the universe by then."),
    ("Ligent Technologies, Inc.", "9856.HK", "HKEX", "~$4.3B",
     "Tech", "Optical Interconnect", "2026-09-22",
     "IPO (Bucket 1 adjacency &mdash; QUEUED, reply <code>add 9856.HK</code>)",
     "3308.HK, COHR, LITE, AAOI, FN",
     "Hisense-controlled, San Jose-HQ optical transceiver maker; ~70% of revenue is AI-datacenter datacom modules, "
     "800G and 1.6T in volume. Raised ~$723M at HK$32.96, +4.6% day one. 2025 revenue RMB 8.36B (+64%), profit "
     "RMB 873M. Six covered names share the subsector, including Innolight's HK line."),
]

SUMMARIES = [
    ("Electra Therapeutics, Inc. (ETRA)",
     "We develop antibodies that kill the specific immune cells driving a runaway inflammatory response instead of "
     "suppressing the whole immune system. Our lead drug, ipsoprubart, targets SIRP proteins on overactive myeloid "
     "and T cells, and we are testing it first in secondary hemophagocytic lymphohistiocytosis &mdash; a frequently "
     "fatal cytokine storm with no FDA-approved treatment &mdash; in a Phase 2/3 completing enrollment in 2H27. We "
     "have no revenue; IPO proceeds fund the program to an FDA filing, with runway into 2029."),
    ("ADARx Pharmaceuticals, Inc. (ADRX)",
     "We make siRNA drugs that silence the gene behind a disease so the harmful protein is never made. Our lead, "
     "onvuzosiran, shuts down liver production of plasma kallikrein to prevent hereditary angioedema attacks and is "
     "in Phase 3; behind it sit agazisiran for complement-driven kidney and eye disease and a Factor XI program for "
     "stroke prevention. We have no product revenue &mdash; AbbVie paid $335M upfront in 2025 to license our platform "
     "and owes milestones and royalties."),
    ("Ligent Technologies, Inc. (9856.HK)",
     "We make the optical transceivers that convert electrical signals to light and back inside AI data centers and "
     "telecom networks, plus the optical chips inside them and fiber-to-the-home terminals. About 70% of revenue is "
     "datacom modules sold to cloud operators and network-equipment vendors, and we ship 800G and 1.6T parts in "
     "volume. 2025 revenue was RMB 8.36B, up 64%, with RMB 873M of profit; Hisense Group controls us."),
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

<p><b>Window</b> 2026-09-11 &rarr; 2026-09-25 (14d) &middot; <b>Universe</b> 1,354 &rarr; 1,355 &middot;
<b>Action needed:</b> <code>9856.HK</code> &middot; <code>SOUN</code> &middot; <code>EMAT</code> &middot; <code>688836.SS</code></p>

<h3>Decisions</h3>
<ul>
<li><b>Added by rule &mdash; 1.</b> <code>ETRA</code> Electra Therapeutics, Bucket 1. Already in the universe.</li>
<li><b>Qualified by rule, not yet written &mdash; 1.</b> <code>ADRX</code> ADARx; first trade 09-25, vendor currency
not live, enrichment refused the row.</li>
<li><b>Awaiting your reply &mdash; 4.</b> <code>9856.HK</code> Ligent (new); <code>SOUN</code>, <code>EMAT</code>
(3rd week); <code>688836.SS</code> Unitree (6th week, expires ~10-20).</li>
<li><b>Remove &mdash; 2 new, 10 carried.</b> New: <code>TBPH</code> (Zymeworks closed 09-23), <code>DOMO</code>
(business sold to Progress 09-22; the cash/NOL shell trades as <code>HUCK</code>). Carried: <code>APGE</code>
<code>CRNX</code> <code>FBRX</code> <code>LPSN</code> <code>TALK</code> <code>BRNS</code> <code>BTAI</code>
<code>SGMO</code> <code>IOBT</code>; remap <code>VYNE</code>&rarr;<code>YARW</code>.</li>
<li><b>Flagged &mdash; 1.</b> <code>ASBP</code> now reads &ldquo;Aspire-Lakewood Holdings&rdquo; at the vendor.</li>
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
<li><b>Last week's run (09-18) produced no report and no Slack post</b> &mdash; the headless session exited 1.
This report covers both weeks (14-day window).</li>
<li><b>Oura prices 09-30</b>: 50M shares at $40&ndash;44, $15.6B fully diluted. Below the $25B bar; a MedTech
adjacency to judge at pricing. <b>Accelevation</b> (data-center power distribution, $4.9&ndash;5.4B) prices the
same day.</li>
<li><b>Nscale filed its S-1 on 09-18</b>; press puts the target at up to $35B &mdash; Bucket 2 if it prices at
&ge;$25B.</li>
<li><b>Postponed:</b> Holtec Nuclear ($9.4B, citing the AI-datacenter selloff), Bamboo Insurance, Amaero.</li>
<li><b>Dangote Refinery</b>: offer closes 10-13, NGX listing expected November at $49B &mdash; Bucket 2 on listing.
<b>Anthropic</b>'s public S-1 had not appeared as of 09-25.</li>
<li><b>Confidential-submission ledger +2:</b> Sensorion (already covered as <code>ALSEN.PA</code>; a US ADS listing)
and Westinghouse (company release 07-31, missed by the August sweeps).</li>
<li>Two measured misses from the IPO calendar and Gmail: <code>NWCL</code> newcleo and <code>ONEN</code> ONE Nuclear,
both de-SPACs, both out of sector.</li>
<li>Schwab again produced no new-issue alerts; the subscription is probably off.</li>
</ul>

<h3>CSV Changes</h3>
<p><b>Added:</b> <code>ETRA</code> Electra Therapeutics Inc. (Biopharma / Biotech), auto-add, Bucket 1. Row count
<b>1,354 &rarr; 1,355</b>. <b>Not added:</b> <code>ADRX</code>, refused by enrichment (see above). <b>Removed:</b>
none; removal candidates await your decision.</p>

<h3>Report Files Generated</h3>
<ul>
<li><code>weekly_coverage_universe_additions_{DATE}.md</code> &mdash; this week's recommendations report</li>
<li><code>company_backgrounds_{DATE}.md</code> &mdash; full background briefings for ETRA, ADRX and 9856.HK</li>
<li><code>data/discovery_output_{DATE}.json</code> &mdash; structured candidate output, schema-validated</li>
<li><code>data/confidential_watch.json</code> &mdash; confidential-submission ledger; two added</li>
<li><code>form10_watch_{DATE}.md</code> &mdash; Form 10-12B spin-off and uplisting watch</li>
<li><code>s1_watch_{DATE}.md</code> &mdash; S-1 / F-1 IPO pipeline watch</li>
<li><code>symbol_directory_{DATE}.md</code> &mdash; US exchange symbol-directory diff and covered-name cross-check</li>
<li><code>delisted_check_{DATE}.md</code> / <code>.csv</code> &mdash; yfinance identity and price-recency probe</li>
<li><code>ticker_change_check_{DATE}.md</code> / <code>.csv</code> &mdash; SEC CIK-keyed ticker-change check</li>
<li><code>foreign_crosscheck_{DATE}.md</code> &mdash; foreign metadata cross-check vs iShares and SEC N-PORT</li>
<li><code>isin_identity_{DATE}.md</code> &mdash; ISIN to issuer-name identity audit via OpenFIGI</li>
<li><code>cik_name_resolution_{DATE}.md</code> &mdash; CIK to SEC-title resolution report</li>
<li><code>coverage_performance_{DATE}.xlsx</code> / <code>coverage_consolidated_{DATE}.html</code> + segment HTMLs
&mdash; produced after this session by the scheduled performance backstop</li>
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
