"""Send the digest over SMTP. Gmail: use an App Password, not your login."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def split_addrs(raw: str | None) -> list[str]:
    """MAIL_TO / MAIL_CC: comma or semicolon separated, blanks dropped."""
    if not raw:
        return []
    out, seen = [], set()
    for part in raw.replace(";", ",").split(","):
        addr = part.strip()
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def send(subject: str, html_body: str) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addrs = split_addrs(os.getenv("MAIL_TO", user)) or [user]
    cc_addrs = [a for a in split_addrs(os.getenv("MAIL_CC", "")) if a.lower() not in {t.lower() for t in to_addrs}]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg.set_content("This digest is HTML. Open it in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    dest = " -> " + ", ".join(to_addrs)
    if cc_addrs:
        dest += "  cc: " + ", ".join(cc_addrs)
    print(f"  mailed{dest}")
