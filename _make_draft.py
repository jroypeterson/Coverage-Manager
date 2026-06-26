"""One-off: build the weekly coverage email and APPEND it to Gmail Drafts via IMAP.

Not part of the package; safe to delete after the run.
"""
import imaplib, os, time
from email.mime.text import MIMEText
from email.utils import formatdate
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
ADDR = os.environ["GMAIL_ADDRESS"]
PW = os.environ["GMAIL_APP_PASSWORD"]
DATE = "2026-06-26"
SUBJECT = f"[Agentic Investing] — Weekly Coverage Universe Additions — {DATE}"

# (rank, company, ticker, exchange, mktcap, sector, subsector, date, trigger, peers, reason)
ROWS = [
    (1, "SK hynix Inc.", "SKHY", "Nasdaq (ADR)", "~$29B ADR raise / ~$170B co.", "Tech",
     "Semiconductors (Memory)", "~2026-07-10 (filed F-1 6/24)", "IPO / ADR listing (&gt;$25B)",
     "NVDA, ADI, ASML, SNDK*",
     "World's #2 memory maker and #1 in HBM (56% Q1'26 share). ~$29B Nasdaq ADR is the largest US listing of 2026; direct play on the AI memory supercycle. Fills a glaring semis-memory gap."),
    (2, "Forgent Power Solutions, Inc.", "FPS", "NYSE", "~$11.5B (Apr-30 Russell; ~$15B+ now)", "Industrials",
     "Electrical Equipment", "2026-02-05 (Russell 1000 add 6/26)", "Russell 1000 addition + strategic",
     "ARXS, CAT, VLTO",
     "Engineered-to-order transformers, switchgear, and transfer switches for AI data centers and the grid — the picks-and-shovels of the power-buildout theme. Up ~2x since its Feb IPO."),
    (3, "Veradermics, Incorporated", "MANE", "NYSE", "~$4.3–5B", "Biopharma",
     "Dermatology", "2026-02-04 (Russell 2000 add 6/26)", "Russell 2000 addition + healthcare",
     "(derm / specialty pharma)",
     "Late-clinical aesthetic-dermatology pharma; lead VDPHL01 hit Phase 2/3 and could be the first FDA-approved oral pattern-hair-loss drug in ~30 years. Largest HC name in the June Russell IPO class ($4.1B)."),
    (4, "SOLV Energy, Inc.", "MWH", "Nasdaq", "~$6.7B", "Energy",
     "Renewables (Solar/Storage EPC)", "2026-02-11 (Russell 1000 add 6/26)", "Russell 1000 addition + strategic",
     "XE, BE",
     "Largest US utility-scale solar + battery-storage EPC / O&amp;M provider; $2.49B 2025 revenue, $149M net income, $8.2B backlog. A profitable operating company (not a developer) levered to the power-demand buildout."),
    (5, "Kardigan, Inc.", "KARD", "Nasdaq", "~$2.0B", "Biopharma",
     "Biotech (Cardiovascular)", "2026-06-18", "IPO", "CYTK",
     "The week's freshest IPO ($400M, priced top of range). Cardiovascular drug developer founded by MyoKardia alumni (Camzyos/$13B BMS deal); lead danicamtiv is a cardiac myosin activator — a direct CYTK-adjacent comp already in the sheet."),
]

SUMMARIES = [
    ("SK hynix Inc. (SKHY)",
     "We are the world's second-largest memory-chip maker and the runaway leader in high-bandwidth memory (HBM), the stacked DRAM that sits next to every AI accelerator — we held ~56% of the HBM market by revenue in Q1 2026 and are Nvidia's primary HBM supplier. FY2025 was a record: ₩97.1T (~$71B) revenue, ₩47.2T operating profit (a 49% operating margin), and ₩42.9T net profit. We are now listing ~$29B of American Depositary Shares on Nasdaq under \"SKHY\" (expected ~July 10) to widen our US investor base — the largest US listing of 2026. The investment question is whether AI-grade memory pricing is a new structural plateau or a cycle peak."),
    ("Forgent Power Solutions, Inc. (FPS)",
     "We design and build the heavy electrical gear that moves power inside data centers, on the grid, and into energy-intensive industrial sites — transformers, switchgear, transfer switches, and prefabricated power rooms, almost all engineered-to-order. We were assembled by private equity (Neos) out of four legacy US manufacturers and sell into the hottest demand pocket in industrials: the AI/cloud data-center buildout and grid electrification. We listed on the NYSE in February 2026 at $27 (a $1.5B IPO) and have roughly doubled; FY26 revenue is guided to ~$1.37B (+82%) with ~$315M adjusted EBITDA and a $2.0B backlog. We make money on long-lead, high-spec custom orders where capacity and qualification — not price — gate supply."),
    ("Veradermics, Incorporated (MANE)",
     "We are a dermatologist-founded, late-clinical specialty-pharma company building branded treatments for high-prevalence aesthetic and dermatologic conditions. Our lead asset, VDPHL01, is an oral, non-hormonal pill for male and female pattern hair loss that posted positive Phase 2/3 topline data in April 2026 — positioning it to potentially become the first FDA-approved oral pattern-hair-loss therapy in nearly 30 years, a market currently served by decades-old generics. We are pre-revenue (Q1'26 net loss ~$27M) with ~$391M cash funding operations toward a potential launch. The thesis is a large, cash-pay, brand-loyal aesthetic market with a clean regulatory path; the risk is single-asset, pre-commercial execution."),
    ("SOLV Energy, Inc. (MWH)",
     "We are the largest EPC and O&amp;M provider for utility-scale solar and battery-storage plants in the United States — we build and then service the power assets rather than owning them. We generated ~$2.49B of revenue and ~$149M of net income in 2025, and carry an ~$8.2B backlog as of March 2026. We make money on construction contracts plus recurring, higher-margin O&amp;M on the installed base. We listed on Nasdaq in February 2026 (~$6.7B cap). The bull case is a profitable, asset-light contractor riding US power-demand growth; the bear case is solar-policy/IRA sensitivity and construction-margin risk."),
    ("Kardigan, Inc. (KARD)",
     "We are a clinical-stage cardiovascular drug developer founded by alumni of MyoKardia (the company behind Camzyos, acquired by Bristol Myers Squibb for $13B). We are advancing three late-stage cardiac assets: danicamtiv (cardiac myosin activator, Phase 2b/3, genetic dilated cardiomyopathy), ataciguat (sGC activator, Phase 2b, calcific aortic valve stenosis), and tonlamarsen (antisense oligonucleotide, Phase 2b, acute severe hypertension) — plus the Prolaio wearable/analytics platform. We IPO'd on Nasdaq on June 18, 2026, raising $400M at $16 for a ~$2.0B cap. We are pre-revenue and binary on 2027 readouts; danicamtiv's mechanism is a direct read-through to Cytokinetics (CYTK), already in the sheet — included for relevance, not as a de-risked holding."),
]

def th(t): return f'<th style="border:1px solid #ccc;padding:6px;background:#f2f2f2;text-align:left;font-size:12px;">{t}</th>'
def td(t): return f'<td style="border:1px solid #ccc;padding:6px;font-size:12px;vertical-align:top;">{t}</td>'

header = "".join(th(h) for h in ["#","Company","Ticker","Exchange","Market Cap","Sector","Subsector","Listing Date","Trigger","Peers in Sheet","Short Reason"])
body_rows = ""
for r in ROWS:
    body_rows += "<tr>" + "".join(td(str(c)) for c in r) + "</tr>"

summ_html = ""
for name, txt in SUMMARIES:
    summ_html += f'<p style="margin:8px 0;"><b>{name}</b><br>{txt}</p>'

HTML = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:#222;">
<h2 style="margin-bottom:2px;">Weekly Coverage Universe Additions — {DATE}</h2>
<p style="color:#666;margin-top:0;font-size:13px;"><b>Prepared by:</b> Coverage Universe Builder &nbsp;|&nbsp; <b>Review window:</b> 2026-06-16 → 2026-06-26 &nbsp;|&nbsp; <b>Recommendations:</b> 5</p>
<p style="font-size:13px;">This week's signal is dominated by two events: <b>SK hynix's filing for a ~$29B Nasdaq ADR listing</b> (largest US listing of the year, expected to trade ~July 10) and the <b>FTSE Russell reconstitution effective after today's close (June 26)</b>, which adds the 2026 IPO class to the Russell 1000/2000. After deduping against the universe — which already holds MiniMed (MMED), Kailera, Alamar, Generate Biomedicines, Eikon, Janus Living, and X-Energy — four genuinely new, relevant names remain (Forgent Power, SOLV Energy, Veradermics, Kardigan), alongside SK hynix. The live IPO calendar was otherwise almost entirely SPAC shells.</p>
<table style="border-collapse:collapse;border:1px solid #ccc;">
<thead><tr>{header}</tr></thead>
<tbody>{body_rows}</tbody>
</table>
<p style="font-size:12px;color:#666;">*SNDK (SanDisk) was last week's pending recommendation and is not yet in the sheet.</p>

<h3>Company Summaries</h3>
{summ_html}

<h3>Notes</h3>
<ul style="font-size:13px;">
<li><b>SK hynix (SKHY)</b> is already public in Korea (KRX: 000660); this is a <b>new US ADR listing</b> not currently in the universe, so it is a valid addition. "Market Cap" shown is the ~$29B ADR raise; the full company is ~$150–200B. Expected first US trade ~July 10 — pre-trading, so the ADR price is not yet market-set.</li>
<li><b>FTSE Russell reconstitution</b> is effective after today's close (June 26, 2026). After deduping, the relevant new names are <b>FPS, MWH, and MANE</b>; the eight Health Care IPO additions were otherwise dominated by names already in the sheet — a sign prior weeks' discovery is catching them.</li>
<li><b>Last week's five recommendations remain pending:</b> Bending Spoons (BSP), SanDisk (SNDK), ERock (EROC), Doncasters (DPC), Parabilis (PBLS). <b>DPC priced/began trading this week</b> (~$46/sh). Confirm whether you want any of last week's plus this week's appended.</li>
<li><b>Excluded by judgment rules:</b> SPAC units/shells; small/off-theme operating IPOs (Deep Fission, First Carolina, DSC); Lime (micromobility, no in-sheet peers); sub-$2B / off-theme Russell adds (Yesway, Once Upon a Farm, Elmet, Bob's, ARKO, AEVEX, Swarmer); additional clinical-stage biotech Russell adds (Avalyn, Spyglass) per the conservative-on-biotech rule.</li>
</ul>

<h3>CSV Changes</h3>
<p style="font-size:13px;">No changes to <code>coverage_universe_tickers.csv</code> this week — the five names above are <b>pending your approval</b>. Tell me which (if any) to append (plus any of last week's still-pending names) and I will add them with matching Ticker / Exchange / Company Name / Sector (JP) / Subsector (JP) conventions.</p>

<h3>Report Files Generated</h3>
<ul style="font-size:13px;">
<li><code>weekly_coverage_universe_additions_{DATE}.md</code> — Recommendations report with candidate table and company summaries.</li>
<li><code>company_backgrounds_{DATE}.md</code> — Full investment-grade background briefings for all five names.</li>
<li><code>coverage_performance_{DATE}.xlsx</code> — Excel performance workbook for the full coverage universe.</li>
<li><code>coverage_consolidated_{DATE}.html</code> — Consolidated HTML performance report (all sectors).</li>
<li><code>coverage_&lt;segment&gt;_{DATE}.html</code> — Per-segment HTML performance reports (Biopharma, MedTech, Healthcare Services, SaaS, Tech, Following: Non-HC, etc.).</li>
<li><code>coverage_pe_vs_growth_{DATE}.png</code> — P/E (TTM) vs forward-2yr-EPS-growth scatter for the positions/research set.</li>
</ul>

<p style="font-size:13px;"><i>Signed,</i><br><b>Coverage Universe Builder</b></p>
</body></html>"""

msg = MIMEText(HTML, "html", "utf-8")
msg["From"] = ADDR
msg["To"] = "jroypeterson@gmail.com"
msg["Subject"] = SUBJECT
msg["Date"] = formatdate(localtime=True)

M = imaplib.IMAP4_SSL("imap.gmail.com")
M.login(ADDR, PW)
M.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
M.logout()
print("Draft appended to [Gmail]/Drafts:", SUBJECT)
