"""One-off: dump bodies of the specific 2026-09-25 sweep hits worth reading.

Safe to delete after the run. E*Trade puts the company name in the BODY, not the
subject, so an alert cannot be classified without this.
"""
import email
import imaplib
import os
import re
import sys
from email.header import decode_header, make_header

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
ADDR = os.environ["GMAIL_ADDRESS"]
PW = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")

TARGETS = [
    ('from:renaissancecapital.com subject:"Winners and Losers" newer_than:6d', 5000),
    ('from:E-tradeAlerts-DoNotReply@etrade.com subject:"New IPO available" newer_than:14d', 3000),
    ('from:Fidelity.Alerts@fidelity.com subject:"Viant" newer_than:14d', 2500),
]


def body_text(msg):
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain":
                try:
                    parts.append(p.get_payload(decode=True).decode(
                        p.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    pass
        if not parts:
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    try:
                        h = p.get_payload(decode=True).decode(
                            p.get_content_charset() or "utf-8", "replace")
                        parts.append(re.sub(r"<[^>]+>", " ", h))
                    except Exception:
                        pass
    else:
        try:
            raw = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", "replace")
            parts.append(raw if msg.get_content_type() == "text/plain"
                         else re.sub(r"<[^>]+>", " ", raw))
        except Exception:
            pass
    t = "\n".join(parts)
    t = re.sub(r"&nbsp;?", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def main():
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(ADDR, PW)
    M.select('"[Gmail]/All Mail"', readonly=True)
    for q, cap in TARGETS:
        typ, data = M.search(None, "X-GM-RAW", '"' + q.replace('"', '\\"') + '"')
        ids = data[0].split() if data and data[0] else []
        print(f"\n{'#'*78}\n# QUERY {q}  ({len(ids)} hit(s))")
        seen = set()
        for i in ids[-6:]:
            typ, d = M.fetch(i, "(RFC822)")
            if not d or not d[0]:
                continue
            msg = email.message_from_bytes(d[0][1])
            subj = str(make_header(decode_header(msg.get("Subject", ""))))
            if (subj, msg.get("Date","")[:16]) in seen:      # Gmail duplicates several of these senders
                continue
            seen.add((subj, msg.get("Date","")[:16]))
            print(f"\n=== {msg.get('Date','')} | {subj}\n")
            print(body_text(msg)[:cap])
    M.logout()


if __name__ == "__main__":
    main()
