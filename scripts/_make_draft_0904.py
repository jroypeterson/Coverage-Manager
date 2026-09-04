"""Build the 2026-09-04 weekly coverage email and APPEND it to Gmail Drafts via IMAP.

One-off, same pattern as scripts/_make_draft_0828.py. Safe to delete after the run.

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
DATE = "2026-09-04"
SUBJECT = f"[ClaudeFin] Coverage Manager — Weekly Coverage Universe Additions — {DATE}"

# Company, Ticker, Exchange, Mkt Cap, Sector, Subsector, Listing date, Trigger, Peers, Reason
RECS = [
    ("SHEIN Global Holdings Limited", "0625.HK", "HKEX", "$26.4B at offer / <b>$20.8B today</b>",
     "Consumer", "E-Commerce", "2026-09-01",
     "IPO (Bucket 2 &mdash; auto-added by rule)",
     "WMT, LULU, CROX, FIVE, CVNA",
     "Priced HK$48.56 on 08-31 against 4.23bn shares = HK$205bn, <b>$26.4B</b>, above the $25B "
     "any-sector bar. The 08-14, 08-21 and 08-28 reports each committed to adding it at pricing, "
     "so it was added. <b>It has since fallen 21%</b> &mdash; at HK$38.14 the same share count is "
     "$20.8B, below the bar. That is the argument for reversing it; reply "
     "<code>decline 0625.HK</code>."),
    ("Liftoff Mobile, Inc.", "LFTO", "NASDAQ", "$3.2B",
     "Tech", "Ad Tech / Mobile Marketing", "2026-06-04",
     "Russell addition (Bucket 5 &mdash; auto-added by rule)",
     "APP, TTD, GOOGL, META",
     "Russell 2000 preliminary IPO addition dated 2026-08-21, effective 2026-09-21. The best "
     "business of the four: FY26 guide $870-880M revenue (+27-28%) at a 59% adjusted-EBITDA "
     "margin, LTM free cash flow $184M (+142%). Trades $18.72 against a $23 offer."),
    ("Sunshine Silver Mining &amp; Refining Co", "SSMR", "NYSE", "$2.6B",
     "Materials", "Precious Metals Mining", "2026-06-04",
     "Russell addition (Bucket 5 &mdash; auto-added by rule)",
     "none in the universe",
     "Russell 2000 preliminary IPO addition, $2.57B on both FMP and yfinance. <b>Pre-revenue</b> "
     "&mdash; a development-stage Idaho silver and antimony restart with first production targeted "
     "2028, and no adjacency to anything in the book. In the universe only because Bucket 5 is "
     "explicitly sector-agnostic. This is the clearest case for gating that rule."),
    ("Neutron Holdings, Inc. (Lime)", "LIME", "NASDAQ", "$2.5B",
     "Industrials", "Micromobility", "2026-07-01",
     "Russell addition (Bucket 5 &mdash; auto-added by rule)",
     "UBER, ACVA, CVNA",
     "Russell 2000 preliminary IPO addition. Shared e-scooters and e-bikes: $928M LTM revenue to "
     "31-Mar-26, +29% in 2025, ~230 cities in 29 countries, 3.1M MAU (+22% y/y). Uber&rsquo;s app "
     "supplies ~14% of revenue. Trades ~55% above the offer midpoint."),
    ("Applied Aerospace &amp; Defense, Inc.", "AADX", "NYSE", "$2.2B",
     "Industrials", "Aerospace &amp; Defense Components", "2026-06-03",
     "Russell addition (Bucket 5 &mdash; auto-added by rule)",
     "ARXS, MDA, HAWK, CAT",
     "Russell 2000 preliminary IPO addition. Space-launch, defense-aviation and precision-strike "
     "structures; 2025 revenue $498.8M (+~25%) against a $17.0M net loss, ~$1.1B backlog, 11 plants "
     "across six states. Sits next to the space/defense cluster already in the book. Trades $12.53 "
     "against a $20 June offer."),
]

PIPELINE = [
    ("SB Energy", "(Nasdaq)", "IPO", "NVIDIA committed $1.5B", "S-1 filed 2026-09-01",
     "Integrated data-centre and power-infrastructure developer; 8.8 GW-IT of leased campuses and "
     "5.5 GWac of power operating or contracted. SoftBank and OpenAI are strategic investors and "
     "customers. Plausible Bucket 2 add on pricing. H1-26 revenue $138.7M vs $83.3M, net loss "
     "$3.19B."),
    ("Anthropic", "(US)", "IPO", "raise sized above SpaceX&rsquo;s $86.2B",
     "S-1 flip after Labor Day; late Sept / early Oct",
     "Bucket 2 on any plausible size. Renaissance Capital 08-30 puts it on track for a late-September "
     "or early-October listing."),
    ("Oura", "(US)", "IPO", "&lsquo;well above $11B&rsquo;", "fall 2026",
     "Smart-ring maker; the nearest MedTech-adjacent name in the forward book and the one pipeline "
     "deal that would fit the core universe rather than merely clear a size bar. WSJ 08-25."),
    ("Aggreko", "(NYSE)", "IPO", "est. $1.5B raise", "filed 2026-08-24",
     "Mobile power generation and temperature control, filing into the data-centre build-out. "
     "Renaissance Capital called it the week&rsquo;s largest filing. Industrials; no bucket unless "
     "it prices very large."),
    ("Electra Therapeutics", "(US)", "IPO", "$100M raise filed", "filed 2026-08-28",
     "<b>Bucket 1 on pricing</b> &mdash; core sector (Biopharma), no size floor, so this auto-adds "
     "the moment it has a market cap."),
    ("Wella", "(US)", "IPO", "$100M raise filed", "filed 2026-08-31",
     "Professional beauty; KKR-controlled. No bucket unless it prices at or above $25B."),
    ("Inspire Brands", "(US)", "IPO", "not disclosed", "as early as YE 2026",
     "Dunkin&rsquo; owner. Bucket 2 only if it prices at or above $25B; no range published."),
    ("CoVolt / Orion180 / Holtec Nuclear", "(US)", "IPO", "not disclosed", "post-Labor Day launches",
     "Named by Renaissance Capital 08-30 as primed to launch. None has a published range yet."),
    ("Atrium Therapeutics", "(US)", "Form 10", "unknown until it trades", "filed 2026-01-30",
     "Bucket 1 &mdash; core sector (Biopharma, SIC 2834), no size floor. Listing kind unresolved."),
    ("First Tracks Biotherapeutics", "(US)", "Form 10", "unknown until it trades", "filed 2026-03-03",
     "Bucket 1 &mdash; core sector, no size floor. Spin-off from AnaptysBio."),
    ("Octave Intelligence", "(US)", "Form 10 uplisting", "unknown until it trades", "filed 2026-02-11",
     "Already quoted OTC with a CERT filed &mdash; an uplisting, not a spin-off. Tech adjacency."),
    ("Mobility Global", "(US)", "Form 10", "unknown until it trades", "filed 2026-05-07",
     "Adjacent sector (Tech, SIC 7389). Listing kind unresolved."),
]

# Company, Ticker, Cap, Why not
EXCLUDED = [
    ("Evolution Metals &amp; Technologies", "EMAT", "$1.95B (FMP) / $2.05B (yfinance)",
     "<b>Russell 2000 addition straddling the $2B Bucket 5 floor.</b> The bar runs straight through "
     "the vendor disagreement. I could have cleared it by choosing yfinance, which is exactly why I "
     "did not &mdash; picking the source that produces the outcome is not measurement. "
     "<code>auto_add</code>&rsquo;s own asymmetry says a wrong queue costs one reply and a wrong "
     "auto-add costs a row in the fleet&rsquo;s most-depended-on artifact. Reply <code>add EMAT</code>."),
    ("DPC Holdings (Doncasters)", "DPC", "$7.1B",
     "Russell <b>1000</b> preliminary addition, comfortably inside the $2-20B band &mdash; but you "
     "declined it and the ledger row reads <code>[JP] ignore</code>. Kept out of the discovery output "
     "by hand. Nothing in the code would have stopped it; see the Notes."),
    ("ERock", "EROC", "$3.6B",
     "Russell 2000 preliminary addition, inside the band, same situation: declined, "
     "<code>[JP] ignore</code>, kept out by hand."),
    ("Hornbeck Offshore Services", "HOS", "$1.38B",
     "<b>A real NYSE IPO on 2026-09-02 that the Finnhub calendar missed entirely</b> across the whole "
     "08-22 to 09-20 window, and that Gmail missed too &mdash; only the directory diff caught it. "
     "Excluded on the merits: offshore energy-services vessels, no adjacency, below the $2B floor. "
     "The miss is the point."),
    ("Xtend AI Robotics", "XTND", "~$1.5B deal value",
     "Reverse merger with JFB Construction closed 09-03, NYSE debut 09-04, $110M raised. Israeli "
     "defense drones and ground robots. Below the $2B floor and the wrong end of the defense book."),
    ("Exascale Labs", "XLAB", "$0.30B", "GPU-cloud infrastructure, Nasdaq 08-28. Below the floor."),
    ("Scorpio Gold", "SGLD", "$1.74B", "ADR listing 08-26; gold mining, no adjacency, below the floor."),
    ("TurboGen", "TRBG", "$0.28B", "Below the floor."),
    ("Gix Internet", "GIXI", "$0.02B", "Below the floor."),
    ("11 Russell adds below the $2B floor", "ITG BRUN FRBT LCLN REF WHK FCBM SUJA REA + 5 Microcap-only",
     "$20M &ndash; $1.60B",
     "Verified Russell additions but outside the Bucket 5 band. The five Microcap-only names "
     "(GLND, AMCI, NAMM, VWAV, VIDA) are additionally out of scope &mdash; Bucket 5 names the Russell "
     "2000 and 1000, not Microcap."),
    ("Paradium.AI", "PAAI", "$42M", "<b>Not a listing</b> &mdash; The Arena Group (AREN) renamed. FMP still resolves PAAI to &lsquo;Arena Group Holdings, Inc.&rsquo;"),
    ("SANGRIX", "SGRX", "&mdash;", "<b>Not a listing</b> &mdash; Bit Origin (BTOG) renamed 09-03 in an AI-infrastructure pivot."),
    ("YYForce", "YFOR", "$4M", "<b>Not a listing</b> &mdash; YY Group Holding (YYGH) renamed."),
    ("JAB Acquisition Corp I", "ATLQ", "&mdash;", "<b>Not a listing</b> &mdash; JAB renamed. A SPAC either way."),
    ("Advasa Holdings", "ADBT", "~$470M", "Priced 08-25, outside the window; excluded on 08-28 and unchanged."),
    ("14 blank-check SPACs", "&mdash;", "&mdash;", "No operating business."),
]

REMOVALS = [
    ("CRNX", "Crinetics Pharmaceuticals",
     "<b>Vertex merger closed 2026-09-01</b>, $85.00/share, ~$10.0B equity value",
     "Eight S-8 POS filed 09-01; Vertex press release",
     "<b>Remove</b> &mdash; awaiting your reply"),
    ("APGE", "Apogee Therapeutics",
     "<b>AbbVie merger closed 2026-09-03</b>, $135.11/share, ~$10.9B equity value",
     "<b>Form 25-NSE filed 09-03</b>, plus POSASR and five S-8 POS",
     "<b>Remove</b> &mdash; awaiting your reply"),
    ("BCAB", "BioAtla",
     "Nasdaq <b>suspended trading 2026-08-31</b> on bid-price and $2.5M stockholders&rsquo;-equity "
     "failures; now quoted OTC under the same symbol at $2.9M",
     "Nasdaq Hearings Panel determination; Form 25-NSE",
     "Delisted but <b>not deregistered</b> &mdash; your call"),
    ("TALK", "Talkspace",
     "Form 15-12G filed 2026-08-27 after the UHS acquisition &mdash; deregistration confirmed",
     "Flagged on 08-28, still in the universe",
     "<b>Remove</b> &mdash; still awaiting your reply"),
]

SUMMARIES = [
    ("SHEIN Global Holdings Limited (0625.HK)",
     "We sell clothes on the internet, very cheaply, and the interesting part is not the clothes. We "
     "design and source apparel, beauty and homeware from several thousand contract manufacturers "
     "concentrated around Guangzhou and sell direct to consumers in the US, Europe, the Middle East "
     "and Asia through our own apps and websites, earning the gross margin between what we pay a "
     "factory and what a shopper pays us, plus commission from third-party sellers on our "
     "marketplace. What makes it work mechanically is the size of the first production run: we start "
     "at a few hundred units, watch what moves in the first days and reorder only that, which lets us "
     "carry hundreds of thousands of live SKUs without the markdown risk that defines conventional "
     "apparel retail. The model was also built on de minimis parcel exemptions that regulators are "
     "closing, which is why a company privately marked at $100bn in 2022 listed at roughly a quarter "
     "of that."),
    ("Liftoff Mobile, Inc. (LFTO)",
     "We are the toll booth between mobile apps that want users and mobile apps that want revenue, "
     "and we take a cut of everything that crosses. App developers &mdash; overwhelmingly mobile "
     "games, plus e-commerce and subscription apps &mdash; buy installs from us; app publishers let "
     "us fill their ad slots; our Cortex machine-learning stack decides in real time which creative "
     "goes to which person in which app, and we earn a margin on the media spend routed through it. "
     "That was $220M of revenue in Q2 2026, up 35% year on year and our eleventh consecutive quarter "
     "of growth, converting to $132M of adjusted EBITDA at a 60% margin. Because the bidding, "
     "creative and attribution infrastructure is all ours, incremental spend arrives at close to pure "
     "contribution &mdash; which is why trailing-twelve-month free cash flow is $184M, up 142%."),
    ("Applied Aerospace &amp; Defense, Inc. (AADX)",
     "We make the metal that has to not fail. We design, engineer and manufacture structures and "
     "assemblies for space launch, defense aviation and precision-strike programmes &mdash; parts "
     "that have to survive launch loads, re-entry heat and high-g flight &mdash; for prime "
     "contractors and newer space and defense companies that cannot build to those tolerances "
     "themselves. We win a position on a platform, earn revenue delivering against a multi-year "
     "schedule, then earn again on aftermarket sustainment for decades, which is the higher-margin "
     "and quieter half of the model. We run 11 purpose-built plants across six states and over 1.5 "
     "million square feet; 2025 revenue was $498.8M, up nearly 25%, against a $17M net loss, with "
     "roughly $1.1B of orders already booked."),
    ("Neutron Holdings, Inc. (LIME)",
     "We rent electric scooters and bikes by the minute, and underneath the consumer app it is a "
     "fleet-utilisation business. A rider unlocks a vehicle on the street and pays an unlock fee plus "
     "a per-minute rate; a further ~14% of our revenue arrives through Uber&rsquo;s app under a "
     "partnership. We operated in about 230 cities across 29 countries at the end of 2025 and served "
     "3.1 million monthly active users in Q1 2026, on $928M of revenue for the twelve months to March "
     "2026 after growing 29% in 2025. The economics turn entirely on how much revenue a vehicle earns "
     "over its service life against what it costs to buy, charge, rebalance and repair &mdash; which "
     "is why we design our own hardware rather than buying it, and why the first generation of this "
     "industry died."),
    ("Sunshine Silver Mining &amp; Refining Company (SSMR)",
     "<b>We are pre-revenue and we do not currently sell anything.</b> We own the Sunshine Mine in "
     "Idaho&rsquo;s Silver Valley &mdash; historically one of the highest-grade primary silver "
     "deposits in the world, idle for decades &mdash; together with an onsite refinery and the major "
     "permits needed for both silver and antimony production, and we are spending the $310.5M we "
     "raised in June 2026 to bring the complex back into production, currently targeted for 2028. "
     "When we produce, we will sell refined silver and antimony at prevailing market prices, so our "
     "earnings will be grade times throughput times spot price minus cost, with no contracted pricing "
     "and no product differentiation. The antimony permit is the strategically interesting part: it is "
     "a US critical mineral with supply concentrated in China."),
]

FILES = [
    ("weekly_coverage_universe_additions_2026-09-04.md", "This week&rsquo;s report: rule-driven adds, the Bucket 5 finding, listing-lane defects, exclusions"),
    ("company_backgrounds_2026-09-04.md", "Full background briefings for all five names added by rule"),
    ("data/discovery_output_2026-09-04.json", "Structured candidate output that feeds the candidate ledger"),
    ("form10_watch_2026-09-04.md", "Form 10-12B registration watch &mdash; 8 registrants, 0 new, 0 inconclusive"),
    ("symbol_directory_2026-09-04.md", "Nasdaq/NYSE symbol-directory diff and covered-name cross-check"),
    ("delisted_check_2026-09-04.md / .csv", "yfinance identity and price-recency probe over the universe"),
    ("ticker_change_check_2026-09-04.md / .csv", "SEC CIK&rarr;ticker mismatch and deregistration scan"),
    ("foreign_crosscheck_2026-09-04.md", "iShares holdings joined to SEC N-PORT for foreign metadata conflicts"),
    ("isin_identity_2026-09-04.md", "OpenFIGI ISIN&rarr;issuer identity audit"),
    ("cik_name_resolution_2026-09-04.md", "CIK name-resolution report"),
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
             'Window 2026-08-28 &rarr; 2026-09-04 (7d) &middot; Universe 1,347 &rarr; 1,352 &middot; '
             '<b>Action needed:</b> <code>APGE</code>, <code>CRNX</code>, <code>BCAB</code>, '
             '<code>TALK</code>, <code>688836.SS</code></p>')

    p.append('<div style="border-left:4px solid #bf8700;background:#fff8e6;padding:10px 14px;margin:0 0 16px;">'
             '<b>Decisions</b>'
             '<ul style="margin:6px 0 0;padding-left:20px;font-size:13px;">'
             '<li><b>Added by rule &mdash; 5</b> &middot; <code>0625.HK</code> Shein $26.4B (Bucket 2); '
             '<code>LFTO</code> <code>AADX</code> <code>LIME</code> <code>SSMR</code> (Bucket 5 Russell). '
             '<b>All already in the universe</b> &mdash; no reply needed to keep them.</li>'
             '<li><b>Remove &mdash; 2 confirmed</b> &middot; <code>APGE</code> AbbVie closed 09-03, '
             '<code>CRNX</code> Vertex closed 09-01. <code>TALK</code> still open from 08-28.</li>'
             '<li><b>Flagged &mdash; 1</b> &middot; <code>BCAB</code> BioAtla suspended off Nasdaq '
             '08-31, now OTC &mdash; remove or keep?</li>'
             '<li><b>Awaiting your reply &mdash; 1</b> &middot; <code>688836.SS</code> Unitree, third '
             'consecutive week.</li>'
             '</ul></div>')

    p.append('<div style="border-left:4px solid #cf222e;background:#fff5f5;padding:10px 14px;margin:0 0 16px;">'
             '<b>The one thing to actually rule on</b>'
             '<p style="margin:6px 0 0;font-size:13px;">Bucket 5 was switched to auto-add on '
             '2026-08-09 on the recorded ground that the only two names you have ever declined, '
             '<code>DPC</code> and <code>EROC</code>, &ldquo;fall in <b>neither</b> bucket&rdquo;. '
             '<b>Both are on this Russell list, and both are inside $2-20B.</b> The 4-for-4 record '
             'that decision rested on is now 4-for-6. Worse, <code>auto_add</code> never checks for a '
             '<code>declined</code> ledger row &mdash; it refuses tickers already in the universe and '
             'tickers on the provenance removals list, and stops. The only thing that kept DPC and '
             'EROC out this week is that I noticed and removed them by hand. See Notes.</p>'
             '</div>')

    p.append('<h3 style="margin:20px 0 6px;">Added without asking &mdash; 5</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;color:#57606a;">Already in the universe. '
             'Do <b>not</b> reply <code>add</code> for any of these. Universe 1,347 &rarr; 1,352.</p>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in (
        "Company", "Ticker", "Exchange", "Mkt Cap", "Sector", "Subsector",
        "Listing date", "Trigger", "Peers in sheet", "Reason")) + '</tr>')
    for r in RECS:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')

    p.append('<h3 style="margin:22px 0 6px;">Recommendations</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><b>None this week beyond the five '
             'rule-driven adds above.</b> Nothing cleared Bucket 4, and no core-sector listing priced '
             'in the window. The sweep ran in full &mdash; Finnhub IPO calendar 08-22 to 09-20 (32 '
             'rows), the Gmail IPO lane, both listing lanes, a hand-built 7-day symbol-directory diff, '
             'and FTSE Russell&rsquo;s own reconstitution PDFs.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Pending approval backlog &mdash; 1</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><code>688836.SS</code> Unitree Robotics, '
             '$40.5B, first seen 2026-08-21 &mdash; <b>third consecutive week</b>. Bucket 2 fired but '
             'the write was refused for want of vendor metadata. Nothing expired this week; nothing '
             'has been pending over 60 days. Reply <code>add 688836.SS</code> or '
             '<code>decline 688836.SS</code>.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Covered names that stopped trading</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><b>Two acquisitions closed on covered names '
             'this week and the daily lane reported neither as a delisting.</b></p>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in ("Ticker", "Company", "Event", "Evidence", "Action")) + '</tr>')
    for r in REMOVALS:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')
    p.append('<p style="margin:10px 0 0;font-size:13px;"><b>Why the lane missed them.</b> The '
             'symbol-directory step diffs <b>day over day</b>, so <code>CRNX</code> &mdash; which left '
             'the exchange on 09-01 &mdash; appears only in the 09-01 report and is absent from '
             'today&rsquo;s entirely. I found it by diffing the 08-28 and 09-04 snapshots by hand: '
             '<b>25 new symbols and 18 removals</b> against the daily report&rsquo;s 4 and 5. Second, '
             'SEC&rsquo;s <code>tickers</code> field lags the Form 25 by weeks, so the adjudicator read '
             'both names as <i>&ldquo;listed &mdash; likely a symbol-format mismatch&rdquo;</i>, the '
             'most reassuring wrong answer available. <code>APGE</code> had a Form 25-NSE on file when '
             'the report ran and was still adjudicated <code>listed</code>. The fix is a weekly window '
             'on the covered-name check plus a Form 25 read before trusting <code>tickers</code>; both '
             'in <code>universe/symbol_directory.py</code>. I have changed neither.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Pipeline / filings to monitor</h3>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in (
        "Company", "Venue", "Trigger", "Size", "Timing", "Note")) + '</tr>')
    for r in PIPELINE:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')
    p.append('<p style="margin:8px 0 0;font-size:12px;color:#57606a;">Sizes for the four Form 10 rows '
             'are unknown by construction &mdash; no shares trade before separation, so none is an add '
             'and none carries an estimated cap. Shein is off this list: it priced 08-31, listed 09-01 '
             'and was added, which closes the commitment the 08-14, 08-21 and 08-28 reports made.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Listing-lane findings</h3>')
    p.append('<p style="margin:0 0 8px;font-size:13px;"><b>Form 10 lane.</b> 8 registrants, 0 new, 6 '
             'relevant, <b>0 inconclusive</b> &mdash; the bucket where a missed listing hides is empty. '
             'The carry-forward defect diagnosed on 08-28 is unchanged: FedEx Freight and Honeywell '
             'Aerospace both still report &ldquo;(still open)&rdquo; despite having separated and being '
             'covered, because <code>carry_forward</code> closes on a ticker a Form 10-12B cannot '
             'contain.</p>')
    p.append('<p style="margin:10px 0 0;font-size:13px;"><b>New-listing completeness check.</b> The '
             '08-28 &rarr; 09-04 diff shows 25 new symbols. <b>One is an operating-company IPO the '
             'Finnhub sweep missed</b> &mdash; <code>HOS</code> Hornbeck Offshore, a $1.38B NYSE listing '
             'on 09-02. <b>Four are renames, not listings</b>: PAAI&larr;AREN, SGRX&larr;BTOG, '
             'YFOR&larr;YYGH, ATLQ&larr;JAB. Four of 25 this week against two of 21 last week &mdash; '
             'this is now a standing class, and anything reading the directory feed as a listing signal '
             'will double-count. The remaining 20 are 14 SPAC lines, 2 fund rights issues and 4 genuine '
             'small listings.</p>')
    p.append('<p style="margin:10px 0 0;font-size:13px;"><b>Nasdaq financial-status flags.</b> 80 '
             'covered names carry a deficiency code &mdash; 78 <code>D</code>, 2 <code>H</code> '
             '(deficient <i>and</i> delinquent: <code>BRTX</code>, <code>CELU</code>). Almost entirely '
             'sub-$100M Biopharma; no new code on a name above micro-cap, so nothing here changes a '
             'coverage decision.</p>')

    p.append('<h3 style="margin:22px 0 6px;">Considered and excluded</h3>')
    p.append('<table style="border-collapse:collapse;width:100%;">')
    p.append('<tr>' + ''.join(_hdr(h) for h in ("Company", "Ticker", "Mkt Cap", "Why not")) + '</tr>')
    for r in EXCLUDED:
        p.append('<tr>' + ''.join(_cell(c) for c in r) + '</tr>')
    p.append('</table>')

    p.append('<h3 style="margin:22px 0 6px;">Company Summaries</h3>')
    for title, body in SUMMARIES:
        p.append(f'<p style="margin:0 0 4px;"><b>{title}</b></p>')
        p.append(f'<p style="margin:0 0 14px;font-size:13px;">{body}</p>')

    p.append('<h3 style="margin:22px 0 6px;">Notes</h3>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>Shein cleared the bar at pricing and does '
             'not clear it today.</b> The number that fired Bucket 2 is the IPO valuation: HK$48.56 '
             '&times; 4.23bn shares = HK$205bn, $26.4B. The rule reads &ldquo;any IPO globally with '
             'market cap &ge; $25 billion&rdquo;, which is a statement about the offering, and three '
             'consecutive prior reports committed to adding it at pricing. At HK$38.14 today the same '
             'share count is $20.8B. Two mechanical notes for whoever reads the row next: Yahoo '
             'publishes marketCap HK$107.2bn for <code>0625.HK</code>, which is the Class B line only '
             'and roughly half the company; and the HK board designates it SHEIN-W, the '
             'weighted-voting-rights suffix.</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>The Russell list was verified against the '
             'primary source.</b> A secondary site surfaced it; I pulled FTSE Russell&rsquo;s own '
             'preliminary PDFs (<code>preliminary-ipo-additions-3-qtr-r1000.pdf</code> and '
             '<code>-r2000.pdf</code>, both &ldquo;Data as at: 21 August 2026&rdquo;) and they '
             'reproduce exactly &mdash; 4 into the Russell 1000, 27 into the Russell 2000, effective '
             '2026-09-21. Every auto-added cap agrees across FMP and yfinance to within 2%. Worth '
             'noting the list was published 2026-08-21, inside last week&rsquo;s window, and last '
             'week&rsquo;s report did not mention it. That was a miss, not a quiet week.</p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>Two questions on Bucket 5, and both are '
             'yours.</b> First: should <code>auto_add</code> treat a <code>declined</code> ledger row as '
             'a hard refusal, the way it already treats the provenance removals list? I think obviously '
             'yes &mdash; a decline is a decision, and without it a name you threw back under one bucket '
             'is auto-added the moment a vendor re-proposes it under another. Second: should Bucket 5 '
             'keep &ldquo;ANY sector&rdquo;? The rule as written produced <code>SSMR</code> this week &mdash; '
             'a pre-revenue silver miner that will not produce until 2028 and has no adjacency to '
             'anything in the book. If Russell adds should be filtered to sectors near the universe, say '
             'so and Bucket 5 becomes a gated auto-add. If you want the wide net with reversal-by-reply '
             'as the control, that is fine too &mdash; but DPC and EROC say the net catches things you '
             'throw back. <b>I have changed no code.</b></p>')
    p.append('<p style="margin:0 0 10px;font-size:13px;"><b>The IPO calendar missed a $1.4B NYSE '
             'listing.</b> Hornbeck Offshore (<code>HOS</code>) priced and listed on 2026-09-02 and does '
             'not appear anywhere in the Finnhub calendar across 08-22 to 09-20, nor in the Gmail lane. '
             'Only the exchange-directory diff caught it. It is excluded on the merits, but a primary IPO '
             'source that silently omits a $1.4B NYSE deal is worth knowing about.</p>')

    p.append('<h3 style="margin:22px 0 6px;">CSV Changes</h3>')
    p.append('<p style="margin:0 0 6px;font-size:13px;"><b>5 additions, 0 removals.</b> '
             '<code>data/coverage_universe_tickers.csv</code> 1,347 &rarr; 1,352 rows. All five were '
             'added by rule, none by your approval.</p>')
    p.append('<ul style="margin:0 0 10px;padding-left:20px;font-size:13px;">'
             '<li><b>Added</b> <code>0625.HK</code> Shein &mdash; HKEX, Consumer / E-Commerce</li>'
             '<li><b>Added</b> <code>LFTO</code> Liftoff Mobile &mdash; NASDAQ, Tech / Ad Tech &amp; Mobile Marketing</li>'
             '<li><b>Added</b> <code>AADX</code> Applied Aerospace &amp; Defense &mdash; NYSE, Industrials / Aerospace &amp; Defense Components</li>'
             '<li><b>Added</b> <code>LIME</code> Neutron Holdings &mdash; NASDAQ, Industrials / Micromobility</li>'
             '<li><b>Added</b> <code>SSMR</code> Sunshine Silver Mining &amp; Refining &mdash; NYSE, Materials / Precious Metals Mining</li>'
             '<li><b>Pending your decision, not applied:</b> removal of <code>APGE</code>, '
             '<code>CRNX</code> and <code>TALK</code>; and a decision on <code>BCAB</code>, which is '
             'delisted to OTC but not deregistered. A removal is never made without approval.</li></ul>')

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
