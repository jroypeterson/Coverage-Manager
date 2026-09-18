"""One-off: run the weekly_coverage_prompt.md Gmail sweep for 2026-09-18 via IMAP.

Safe to delete after the run. Uses Gmail's X-GM-RAW so the prompt's search strings
are passed VERBATIM rather than paraphrased into subject-only terms -- the Fidelity
subject line contains no "IPO" string at all.
"""
import email
import imaplib
import os
from email.header import decode_header, make_header

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
ADDR = os.environ["GMAIL_ADDRESS"]
PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")

QUERIES = [
    # broker new-issue alerts -- run verbatim per the prompt
    'from:Fidelity.Alerts@fidelity.com ("New Issue" OR Participation) newer_than:10d',
    'from:E-tradeAlerts-DoNotReply@etrade.com ("New Issue" OR IPO OR offering) newer_than:10d',
    'from:noreply@robinhood.com (IPO OR "request shares" OR roadshow) newer_than:10d',
    'from:schwab.com ("new issue" OR IPO) newer_than:10d',
    # IPO summary / calendar sources
    'subject:"IPO" newer_than:10d',
    'subject:"spin-off" OR subject:"spinoff" OR subject:"carve-out" OR subject:"direct listing" newer_than:10d',
    '("IPO" OR "newly public" OR "new listing" OR "priced its IPO") newer_than:10d',
    'from:renaissancecapital.com newer_than:10d',
    'from:streetaccount.com (IPO OR spin OR listing) newer_than:10d',
    'subject:"Russell" OR "index addition" OR "Russell reconstitution" newer_than:10d',
    '"confidentially submitted" OR "confidential submission" OR "has confidentially filed" newer_than:10d',
]


def hdr(msg, key):
    v = msg.get(key, "")
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def main():
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(ADDR, PW)
    M.select('"[Gmail]/All Mail"', readonly=True)
    for q in QUERIES:
        # Inner double quotes must be backslash-escaped or Gmail's IMAP parser
        # rejects the whole command ("BAD Could not parse command").
        typ, data = M.search(None, "X-GM-RAW", '"' + q.replace('"', '\\"') + '"')
        ids = data[0].split() if data and data[0] else []
        print(f"\n{'='*78}\nQUERY: {q}\n  -> {len(ids)} hit(s)")
        for i in ids[-25:]:
            typ, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if not d or not d[0]:
                continue
            msg = email.message_from_bytes(d[0][1])
            print(f"  [{hdr(msg,'Date')[:31]}] {hdr(msg,'From')[:44]}")
            print(f"      {hdr(msg,'Subject')[:150]}")
    M.logout()


if __name__ == "__main__":
    main()
