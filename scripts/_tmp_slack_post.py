import os
import sys

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()
webhook = os.environ.get("SLACK_WEBHOOK_URL")
if not webhook:
    raise SystemExit("SLACK_WEBHOOK_URL not set")

TEXT = """[Agentic Investing] — Weekly Coverage Universe Additions — 2026-07-03

3 recommendations this week:

1. BSP (Bending Spoons) — IPO 7/1 — Largest software IPO of 2026 (~$23B, +40% debut). Profitable, 93%-subscription digital roll-up (AOL, Evernote, Vimeo, WeTransfer); $1.31B 2025 rev. CSU is the in-sheet analog.
2. DPC (Doncasters Group) — IPO 6/25 — 248-year-old precision-cast superalloy maker for jet-engine + gas-turbine hot sections (~$7B, +44% debut). Rides aero ramp + data-center power buildout.
3. RKLB (Rocket Lab) — New candidate — $8B Iridium acquisition (6/29) makes it the vertically integrated challenger to SpaceX. Completes the space cohort around SPCX/MDA/YSS/HAWK. Note: ~$58B, above the usual $2-20B bucket.

Also flagged: SK hynix (SKHY, $29.4B ADR) trades ~July 10. Comcast announced NBCU+Sky spin-off (watch). Universe M&A: Bio-Techne (TECH, Core) -> Merck KGaA $11.4B; Apogee (APGE) -> AbbVie.

CSV changes: none this week (10 prior-week recommendations still pending approval).

Full report + performance files emailed (Gmail draft). Reports folder:
https://www.dropbox.com/home/Claude%20Folder/Coverage%20Manager/reports"""

r = requests.post(webhook, json={"text": TEXT}, timeout=30)
print("Slack post:", r.status_code, r.text[:200])
