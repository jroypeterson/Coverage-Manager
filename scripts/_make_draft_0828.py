"""Build the 2026-08-28 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft_0821.py. Safe to delete after the run.

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
DATE = "2026-08-28"
SUBJECT = f"[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — {DATE}"

# Company, Ticker, Exchange, Mkt Cap, Sector, Subsector, Listing date, Trigger, Peers, Reason
RECS = [
    ("Honeywell Aerospace Inc.", "HONA", "NASDAQ", "$50.9B",
     "Industrials", "Aerospace &amp; Defense", "2026-06-29",
     "Spin-off (Bucket 3 — auto-added by rule)",
     "ARXS, MDA, HAWK, GEV, CAT, FDX",
     "A separation at $50.9B is more than five times the $10B Bucket 3 bar, so the rule "
     "fired and the name is already in the universe — no reply needed. The finding is that "
     "it is eight weeks late: Honeywell Aerospace has traded on Nasdaq since 2026-06-29 and "
     "the Form 10 lane reported it as pipeline, &lsquo;size unknown until it trades&rsquo;, "
     "in every weekly report since. carry_forward() closes a filing when the registrant "
     "ticker is in the universe, but a Form 10-12B never carries a ticker, so the guard can "
     "never fire. FedEx Freight has the same open row and is already covered."),
]

PIPELINE = [
    ("Shein", "(HKEX)", "IPO", "$26-27B, ~$1.8B raise", "prices 08-31, trades 09-01",
     "Above the $25B bar across the FULL HK$47.6-49.5 range — the bottom of the range is "
     "~$26.0B. A mandatory Bucket 2 add the moment it prices. This restates the 08-14 and "
     "08-21 calls rather than reversing them."),
    ("Oura", "(US)", "IPO", "&lsquo;well above $11B&rsquo;", "fall 2026",
     "Smart-ring maker; the nearest MedTech-adjacent name in the forward book. Bucket 4 "
     "candidate on size, and the one pipeline deal that would fit the core universe rather "
     "than merely clear a size bar. Source: WSJ 08-25."),
    ("Anthropic", "(US)", "IPO", "raise sized to top SpaceX&rsquo;s $86.2B", "October; filing not yet public",
     "Bucket 2 on any plausible size. Reported 14x increase in quarterly revenue. Confidential "
     "S-1 is in; nothing public to price against yet."),
    ("Inspire Brands", "(US)", "IPO", "not disclosed", "as early as YE 2026",
     "Dunkin&rsquo; owner. Bucket 2 only if it prices at or above $25B; no range published."),
    ("Atrium Therapeutics", "(US)", "Form 10", "unknown until it trades", "filed 2026-01-30",
     "Bucket 1 — core sector (Biopharma, SIC 2834), no size floor. Listing kind unresolved: "
     "no parent named and SEC shows no OTC quotation."),
    ("First Tracks Biotherapeutics", "(US)", "Form 10", "unknown until it trades", "filed 2026-03-03",
     "Bucket 1 — core sector, no size floor. Spin-off from AnaptysBio."),
    ("Octave Intelligence", "(US)", "Form 10 uplisting", "unknown until it trades", "filed 2026-02-11",
     "Already quoted OTC with a CERT filed — an uplisting, not a spin-off. Tech adjacency."),
    ("Mobility Global", "(US)", "Form 10", "unknown until it trades", "filed 2026-05-07",
     "Adjacent sector (Tech, SIC 7389). Listing kind unresolved."),
]

# Company, Ticker, Cap, Why not
EXCLUDED = [
    ("Endovia Health Sciences", "EDVA", "$2.1M",
     "NOT a new listing — Splash Beverage Group (SBEV) renamed 08-24. Bucket 1 auto-adds a "
     "core-sector listing at any size, and FMP classifies this Healthcare, so the rule is "
     "against me here and I am excluding it deliberately: no shares were issued, no "
     "registration was effected, holders kept their certificates. A rename is not a listing "
     "event. If it were, every rebrand would auto-add a duplicate of a covered company."),
    ("Senmiao Technology", "VAI", "$26.9M",
     "NOT a new listing — AIHS renamed, same week, same argument as EDVA."),
    ("Versigent", "VGNT", "$3.4B",
     "Spin-off from Aptiv (2026-04-01) but below the $10B Bucket 3 bar. Wiring harnesses and "
     "electrical distribution — no adjacency to healthcare, tools, semis or instrumentation."),
    ("Midera Food Processing", "MFP", "$2.2B",
     "Spin-off from Middleby (2026-07-06), below $10B. Food-processing equipment; no adjacency."),
    ("ADI Global Distribution", "ADIG", "$1.5B",
     "Spin-off below $10B and below the $2B Bucket 4 floor."),
    ("Lyntris", "LYNX", "~$1.4B",
     "Priced $17.50 against a $19-22 range, cut to 17M shares, now $14.17. Below the $2B "
     "Bucket 4 floor — consistent with the 08-21 call."),
    ("Sunbelt Rentals", "SUNB", "~$29B",
     "NOT a separation — Ashtead redomiciled into a US holding company with a dual LSE/NYSE "
     "listing on 2026-03-02. No new business was separated, so Bucket 3 does not apply."),
    ("Advasa Holdings", "ADBT", "~$470M", "Consumer-credit services; no bucket fires."),
    ("First Breach", "FBDT", "$43.7M", "Micro-cap defense; no bucket fires."),
    ("9 blank-check SPACs", "&mdash;", "&mdash;", "No operating business."),
]

SUMMARIES = [
    ("Honeywell Aerospace Inc. (HONA)",
     "We make the systems that fly, guide and power aircraft: avionics and flight-deck "
     "software, auxiliary power units, propulsion engines, wheels and brakes, and satellite "
     "and guidance hardware. We sell to airframers such as Airbus and Boeing and to defence "
     "primes and governments, but the money is in the installed base &mdash; 45% of our "
     "revenue is commercial aftermarket, where we sell spares, repairs and upgrades against "
     "equipment already flying, at far better margins than the original shipset. We book new "
     "equipment near cost to win a platform, then earn on it for the thirty years it stays in "
     "service, which is why our $18.2 billion backlog matters more than any single "
     "quarter&rsquo;s shipments."),
]

FILES = [
    ("weekly_coverage_universe_additions_2026-08-28.md", "This week&rsquo;s recommendations report, listing-lane findings and exclusions"),
    ("company_backgrounds_2026-08-28.md", "Full background briefing for HONA (financials, bull/bear, what to watch)"),
    ("data/discovery_output_2026-08-28.json", "Structured candidate output that feeds the candidate ledger"),
    ("form10_watch_2026-08-28.md", "Form 10-12B registration watch &mdash; 8 registrants, 0 new, 0 inconclusive"),
    ("symbol_directory_2026-08-28.md", "Nasdaq/NYSE symbol-directory diff and covered-name cross-check"),
    ("delisted_check_2026-08-28.md / .csv", "yfinance identity and price-recency probe over the universe"),
    ("ticker_change_check_2026-08-28.md / .csv", "SEC CIK&rarr;ticker mismatch and deregistration scan"),
    ("foreign_crosscheck_2026-08-28.md", "iShares holdings joined to SEC N-PORT for foreign metadata conflicts"),
    ("isin_identity_2026-08-28.md", "OpenFIGI ISIN&rarr;issuer identity audit"),
    ("cik_name_resolution_2026-08-28.md", "CIK name-resolution report"),
]


def _cell(x):
    return f'<td style="padding:6px 9px;border:1px solid #d5d9e0;vertical-align:top;font-size:13px;">{x}</td>'


def _hdr(x):
    return (f'<th style="padding:6px 9px;border:1px solid #d5d9e0;background:#eef1f5;'
            f'text-align:left;font-size:12px;letter-spacing:.02em;">{x}</th>')


def build_html():
    p = []
    p.append('<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
             'color:#1b1f24;max-width:1000px;line-height:1.5;">')
    p.append(f'<h2 style="margin:0 0 4px;">Weekly Coverage Universe Additions &mdash; {DATE}</h2>')
    p.append('<p style="margin:0 0 14px;color:#57606a;font-size:13px;">'
             'Window 2026-08-21 &rarr; 2026-08-28 (7d) &middot; Universe 1,346 &rarr; 1,347 &middot; '
             '<b>Action needed:</b> <code>688836.SS</code>, <code>TALK</code></p>')

    p.append('<div style="border-left:4px solid #bf8700;background:#fff8e6;padding:10px 14px;margin:0 0 16px;">'
             '<b>Decisions</b>'
             '<ul style="margin:6px 0 0;padding-left:20px;font-size:13px;">'
             '<li><b>Added by rule &mdash; 1</b> &middot; <code>HONA</code> Honeywell Aerospace $50.9B '
             '&mdash; Bucket 3 spin-off, <b>already in the universe</b>, no reply needed</li>'
             '<li><b>Awaiting your reply &mdash; 1</b> &middot; <code>688836.SS</code> Unitree '
             '&mdash; carried from 08-21; reply <code>add</code> or <code>decline</code></li>'
             '<li><b>Flagged &mdash; 1</b> &middot; <code>TALK</code> Talkspace filed Form 15-12G on '
             '08-27 &mdash; deregistration confirmed. Remove the row?</li>'
             '</ul></div>')

    # Recommendations table
    p.append('<h3 style="margin:20px 0 6px;">Added without asking (Bucket 3 rule)</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;color:#57606a;">Already in the universe. '
             'Do <b>not</b> reply <code>add</code> for this name.</p>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in (
        "Company", "Ticker", "Exchange", "Mkt Cap", "Sector", "Subsector",
        "Listing date", "Trigger", "Peers in sheet", "Reason to add")) + '</tr>')
    for r in RECS:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')

    p.append('<h3 style="margin:22px 0 6px;">Recommendations</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><b>None this week beyond the '
             'rule-driven add above.</b> No candidate cleared Bucket 1, 2, 4 or 5. The sweep ran '
             'in full &mdash; Finnhub IPO calendar, Gmail, both listing lanes, and a direct '
             '08-21 &rarr; 08-28 exchange-directory diff.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Pending approval backlog &mdash; 1</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><code>688836.SS</code> Unitree Robotics, '
             '$40.5B, first seen 2026-08-21. Bucket 2 fired but the write was refused for want of '
             'vendor metadata. Nothing expired this week; nothing has been pending over 60 days.</p>')

    # Pipeline
    p.append('<h3 style="margin:22px 0 6px;">Pipeline / filings to monitor</h3>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in (
        "Company", "Venue", "Trigger", "Size", "Timing", "Note")) + '</tr>')
    for r in PIPELINE:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')
    p.append('<p style="margin:8px 0 0;font-size:12px;color:#57606a;">Sizes for the four Form 10 '
             'rows are unknown by construction &mdash; no shares trade before separation, so none '
             'is an add and none carries an estimated cap.</p>')

    # Listing lane findings
    p.append('<h3 style="margin:22px 0 6px;">Listing-lane findings</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><b>Covered names that stopped trading '
             '&mdash; two this week, and the daily report shows only one.</b></p>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in ("Ticker", "Left exchange", "Status", "Action")) + '</tr>')
    for r in [
        ("TALK", "2026-08-17", "Form 15-12G filed 2026-08-27 &mdash; deregistration confirmed after the UHS acquisition", "<b>Remove</b> &mdash; awaiting your reply"),
        ("OSRH", "2026-08-26", "Nasdaq <b>suspended</b> trading on a bid-price failure; hearing requested 08-25, which stays the Form 25", "Do NOT remove &mdash; no deregistration"),
        ("FBRX", "2026-08-28", "Absent from the directory, but SEC still lists it on Nasdaq and it filed six POS AM on 08-27", "Do NOT remove &mdash; corporate action"),
    ]:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')
    p.append('<p style="margin:10px 0 0;font-size:13px;">The symbol-directory step diffs '
             '<b>day over day</b>, so a covered name that leaves the exchange mid-week is named '
             'only in that day&rsquo;s report. <code>OSRH</code> left on 08-26 and appears nowhere '
             'in the 08-28 report; I found it by diffing the 08-21 and 08-28 snapshots directly. '
             'Any covered name that stops trading Monday&ndash;Thursday is invisible to the weekly '
             'report unless someone does that.</p>')
    p.append('<p style="margin:10px 0 0;font-size:13px;"><b>New-listing completeness check.</b> '
             'The week&rsquo;s directory diff shows 21 new symbols and <b>zero are operating-company '
             'IPOs the Finnhub sweep missed</b>: 15 SPAC share/unit/warrant lines, 3 rights or note '
             'issues, 1 IPO already captured (ADBT), and 2 renames of existing listings '
             '(EDVA&larr;SBEV, VAI&larr;AIHS).</p>')
    p.append('<p style="margin:10px 0 0;font-size:13px;"><b>Form 10 lane.</b> 8 registrants, '
             '0 new, 6 relevant, <b>0 inconclusive</b> &mdash; the bucket where a missed listing '
             'hides is empty this week.</p>')

    # Excluded
    p.append('<h3 style="margin:22px 0 6px;">Considered and excluded</h3>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in ("Company", "Ticker", "Mkt Cap", "Why not")) + '</tr>')
    for r in EXCLUDED:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')

    # Summaries
    p.append('<h3 style="margin:22px 0 6px;">Company Summaries</h3>')
    for title, body in SUMMARIES:
        p.append(f'<p style="margin:0 0 4px;"><b>{title}</b></p>')
        p.append(f'<p style="margin:0 0 14px;font-size:13px;">{body}</p>')

    # Notes
    p.append('<h3 style="margin:22px 0 6px;">Notes</h3>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>Why the HONA add is eight weeks late.</b> '
             'The Form 10 lane carries a still-open filing forward until &ldquo;the registrant&rsquo;s '
             'ticker is in the universe&rdquo;. A Form 10-12B never carries a ticker &mdash; no shares '
             'trade before separation, which is the premise the module is built on &mdash; so '
             '<code>data/form10_seen.json</code> stores an empty ticker for every spin-off registrant '
             'and the close condition can never fire. Honeywell Aerospace separated 2026-06-29 at '
             'what is now $50.9B and was reported as &ldquo;size unknown until it trades&rdquo; every '
             'week since. FedEx Freight has the same open row and is already covered &mdash; the '
             'harmless direction of the same bug. Fixing either fixes both: close on the parent plus '
             'separation event, not on a ticker the filing cannot contain. I have not changed that '
             'module; that is your call.</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>Market cap is cross-checked.</b> '
             'FMP $50.948B, yfinance $50.950B, and the distribution itself &mdash; 316,939,750 shares '
             'at $160.75 = $50.95B. Unlike Unitree, the name resolves, so enrichment wrote a complete '
             'row (ISIN US43849R1059, CIK 2089271, composite FIGI BBG020QYV152).</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>One thing to read before believing a '
             'headline.</b> A search summary asserted FBRX completed a take-private on 08-19. The SEC '
             'filing history says otherwise &mdash; six post-effective registration amendments and '
             'Form 4s on 08-27, no Form 15, no Form 25, listing still shown. A company going private '
             'does not amend its registration statements. Treated as unresolved, not delisted.</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>No Russell activity.</b> FTSE Russell adds '
             'eligible IPOs quarterly in March, June, September and December. Nothing landed in this '
             'window; the next quarterly IPO additions are September.</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>A coverage gap worth a decision.</b> '
             '<code>HON</code> Honeywell Technologies &mdash; the ~$76B pure-play automation company '
             'left behind by the spin &mdash; is not in the universe, and no bucket reaches it: '
             'Bucket 4 tops out at $20B and it is not a new listing. Automation and instrumentation '
             'are named interests on the sheet, so this is a judgement for you rather than a rule.</p>')

    # CSV changes
    p.append('<h3 style="margin:22px 0 6px;">CSV Changes</h3>')
    p.append('<p style="margin:0 0 6px;font-size:13px;"><b>1 addition, 0 removals.</b> '
             '<code>data/coverage_universe_tickers.csv</code> 1,346 &rarr; 1,347 rows.</p>')
    p.append('<ul style="margin:0 0 10px;padding-left:20px;font-size:13px;">'
             '<li><b>Added</b> <code>HONA</code> &mdash; Honeywell Aerospace Inc, NASDAQ, '
             'Industrials / Aerospace &amp; Defense. Auto-added by the Bucket 3 rule, not by your '
             'approval.</li>'
             '<li><b>Pending your decision, not applied:</b> removal of <code>TALK</code>. '
             '<code>OSRH</code> and <code>FBRX</code> are deliberately not removed &mdash; neither '
             'has deregistered.</li></ul>')

    # Files
    p.append('<h3 style="margin:22px 0 6px;">Report Files Generated</h3>')
    p.append('<ul style="margin:0 0 10px;padding-left:20px;font-size:13px;">')
    for fn, desc in FILES:
        p.append(f'<li><code>{fn}</code> &mdash; {desc}</li>')
    p.append('</ul>')

    p.append('<p style="margin:16px 0 0;font-size:13px;">Published page: '
             '<a href="https://jroypeterson.github.io/Coverage-Manager/">'
             'jroypeterson.github.io/Coverage-Manager</a><br>'
             'Reports folder: '
             '<a href="https://www.dropbox.com/home/Claude%20Folder/Coverage%20Manager/reports">'
             'Dropbox / Coverage Manager / reports</a></p>')
    p.append('<p style="margin:18px 0 0;font-size:13px;">&mdash; Coverage Universe Builder</p>')
    p.append('</div>')
    return '\n'.join(p)


def main():
    msg = MIMEText(build_html(), "html", "utf-8")
    msg["Subject"] = SUBJECT
    msg["From"] = ADDR
    msg["To"] = "jroypeterson@gmail.com"
    msg["Date"] = formatdate(localtime=True)

    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(ADDR, PW)
    m.append("[Gmail]/Drafts", "\\Draft",
             imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    m.logout()
    print("draft appended to [Gmail]/Drafts")
    print("subject:", SUBJECT)


if __name__ == "__main__":
    main()
