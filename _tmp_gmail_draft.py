import imaplib
import os
import sys
import time
from email.message import EmailMessage

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()
addr, pw = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]

SUBJECT = "[Agentic Investing] — Weekly Coverage Universe Additions — 2026-07-03"

HTML = """\
<html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #222;">
<h2>Weekly Coverage Universe Additions — 2026-07-03</h2>
<p><b>Review window:</b> 2026-06-23 → 2026-07-03 (Finnhub IPO calendar + Gmail inbox + web search)<br>
<b>Recommendations this week:</b> 3</p>
<p>Holiday-shortened but eventful week: <b>Bending Spoons (BSP)</b> completed the largest software IPO of 2026
(+40% debut, ~$23B), <b>Doncasters (DPC)</b> — recommended two weeks ago as a filing — debuted +44% (~$7B),
and <b>Rocket Lab (RKLB)</b> announced an $8B acquisition of Iridium to challenge SpaceX, making it the glaring
missing name in the sheet's space cohort (SPCX, MDA, YSS, HAWK). Two universe names got takeover bids this week
(Bio-Techne, Apogee) and Comcast announced a &gt;$10B NBCUniversal+Sky spin-off (watch item).</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; font-size: 13px;">
<tr style="background: #1a3e5c; color: #fff;">
<th>#</th><th>Company</th><th>Ticker</th><th>Exchange</th><th>Mkt Cap</th><th>Sector</th><th>Subsector</th>
<th>Listing Date</th><th>Trigger</th><th>Peers in Sheet</th><th>Short Reason</th></tr>
<tr>
<td>1</td><td>Bending Spoons S.p.A.</td><td><b>BSP</b></td><td>Nasdaq</td><td>~$22.8B</td><td>Tech</td>
<td>Software / Digital Roll-up</td><td>2026-07-01</td><td>IPO</td><td>CSU, SPOT, NFLX, MSFT</td>
<td>Largest software IPO of 2026 ($1.68B at $29; +40% debut). Profitable, 93%-subscription acquirer-operator of
digital brands (AOL, Evernote, Vimeo, WeTransfer); $1.31B 2025 revenue, 84% CAGR since 2023. Public-market test
case for AI-powered software roll-ups — CSU is the in-sheet analog.</td></tr>
<tr>
<td>2</td><td>Doncasters Group (DPC Holdings)</td><td><b>DPC</b></td><td>NYSE</td><td>~$7.1B</td><td>Industrials</td>
<td>Aerospace &amp; Defense Components</td><td>2026-06-25</td><td>IPO</td><td>ARXS, CAT, MDA, FER</td>
<td>248-year-old maker of precision-cast superalloy components for jet-engine and industrial-gas-turbine hot
sections. Priced above range ($919M raise), +44% debut. Rides both the aero-engine ramp and the gas-turbine /
data-center power buildout; customers: GE Aerospace, P&amp;W, Rolls-Royce, Safran, GE Vernova, Siemens Energy.</td></tr>
<tr>
<td>3</td><td>Rocket Lab Corporation</td><td><b>RKLB</b></td><td>Nasdaq</td><td>~$58B</td><td>Tech</td>
<td>Aerospace &amp; Space</td><td>2020-11-24 (SPAC)</td><td>New candidate ($8B Iridium deal 6/29)</td>
<td>SPCX, MDA, YSS, HAWK</td>
<td>#2 western launch provider going vertically integrated: $8B cash+stock for Iridium buys a live 66-satellite
constellation, global L-band spectrum, and ~$1B recurring service revenue to challenge Starlink. $602M 2025 rev
(+38%), $2.2B backlog, Neutron ~Q4'26. Above the usual $2–20B bucket — included on peer logic.</td></tr>
</table>

<h3>Company Summaries</h3>
<p><b>Bending Spoons S.p.A. (BSP)</b><br>
We buy established digital businesses that have gone stale — AOL, Evernote, Vimeo, WeTransfer, Eventbrite,
Remini, StreamYard, komoot — and run them better: we cut costs, rebuild the products with AI, and optimize
pricing and subscription funnels. Ten brands generate over 80% of our revenue, 93% of which is subscriptions.
Revenue compounded at 84% since 2023 ($387M → $671M → $1.31B in 2025) and we are profitable: operating profit
more than doubled to $278M in 2025, and Q1 2026 revenue of $601M was up 2.3x year-over-year. We raised $1.68B
on Nasdaq on July 1 to keep doing this — our screened acquisition pipeline runs to thousands of candidates.</p>
<p><b>Doncasters Group (DPC)</b><br>
We manufacture precision-cast components and nickel- and cobalt-based superalloys for the hottest sections of
jet engines and industrial gas turbines — parts where qualification, not price, gates supply. Founded in
Sheffield in 1778, we run 14 plants with ~3,000 employees and sell to effectively every engine OEM that matters:
GE Aerospace, Pratt &amp; Whitney, Rolls-Royce, Safran on the aero side; GE Vernova, Siemens Energy, Ansaldo,
Doosan on the turbine side. We generated $837M of revenue and $138M of adjusted EBITDA in 2025 (net loss $173M,
largely PE-era financing costs; ~$712M debt being partly repaid with IPO proceeds). We priced above range on
June 25 and trade at ~$7B.</p>
<p><b>Rocket Lab Corporation (RKLB)</b><br>
We are the only company other than SpaceX that launches rockets to orbit at a regular cadence (Electron, 20+
launches/year), and we build satellites and components for others — including an $816M Space Development Agency
missile-warning constellation. We generated $602M of revenue in 2025 (+38%) with a $2.2B backlog and guide to
~$914M in 2026. On June 29 we agreed to acquire Iridium for ~$8B ($54/share cash+stock, closing mid-2027),
adding a live 66-satellite constellation, global L-band spectrum, and ~$1B of recurring service revenue — the
missing pieces to become a vertically integrated challenger to SpaceX/Starlink. Our Neutron medium-lift rocket
targets first launch in Q4 2026.</p>

<h3>Notes</h3>
<ul>
<li><b>Prior-week recommendations still pending in the CSV:</b> BSP, DPC, SNDK, EROC, PBLS (6/19); SKHY, FPS,
MANE, MWH, KARD (6/26). BSP and DPC are now live and re-recommended above.</li>
<li><b>SK hynix (SKHY)</b> — $29.4B ADR raise — expected to begin trading <b>~July 10</b>; re-flag next week once live.</li>
<li><b>M&amp;A hitting the universe:</b> Bio-Techne (TECH, Core) → Merck KGaA $11.4B cash (6/24, +21%);
Apogee Therapeutics (APGE) → AbbVie buyout (+46.7%). Leave both in the CSV until close.</li>
<li><b>Comcast spin-off (Bucket 3 watch):</b> tax-free spin of NBCUniversal + Sky announced 6/29 (CMCSA +23%);
completion ~mid-2027, Comcast keeps 19.9%. No ticker yet — will flag when a when-issued line appears.</li>
<li><b>Pipeline (monitor):</b> Baidu's Kunlunxin targeting HK IPO at ~$50B; Reformation (REF) NYSE filing;
SeeQC (SEQC) quantum filing ($75M — too small); Skims IPO reportedly delayed.</li>
<li><b>Excluded:</b> the week's SPAC flood; Lime (LIME — no in-sheet peers); ITG ($1.9B broadband E&amp;C,
below the bar); Sinda (silver miner); DSC (-57% debut, $20M); CopperTech (copper miner).</li>
</ul>

<h3>CSV Changes</h3>
<p>No changes to the coverage universe CSV this week. The ten prior-week recommendations listed above remain
pending approval.</p>

<h3>Report Files Generated</h3>
<ul>
<li><code>weekly_coverage_universe_additions_2026-07-03.md</code> — Weekly recommendations report with candidate analysis</li>
<li><code>company_backgrounds_2026-07-03.md</code> — Full company background briefings for the 3 recommended companies</li>
<li><code>coverage_performance_2026-07-03.xlsx</code> — Excel performance workbook for the full coverage universe</li>
<li><code>coverage_consolidated_2026-07-03.html</code> — Consolidated HTML performance report (all sectors)</li>
<li><code>coverage_&lt;segment&gt;_2026-07-03.html</code> — Per-segment HTML reports (Biopharma, HC Svcs/MedTech, SaaS, Tech, Following: Non-HC, S&amp;P 500)</li>
<li><code>coverage_pe_vs_growth_2026-07-03.png</code> — P/E (TTM) vs forward-2yr-EPS-growth scatter (positions/research set)</li>
</ul>

<p>— Coverage Universe Builder</p>
</body></html>
"""

msg = EmailMessage()
msg["Subject"] = SUBJECT
msg["From"] = addr
msg["To"] = "jroypeterson@gmail.com"
msg.set_content("HTML report — open in an HTML-capable client.")
msg.add_alternative(HTML, subtype="html")

M = imaplib.IMAP4_SSL("imap.gmail.com")
M.login(addr, pw)
res = M.append(
    '"[Gmail]/Drafts"',
    "\\Draft",
    imaplib.Time2Internaldate(time.time()),
    msg.as_bytes(),
)
print("APPEND result:", res)
M.logout()
