"""Instant notifications via Telegram bot.

Sends real-time formatted alerts to your personal Telegram chat
whenever a new high-fit job is discovered.
"""
from __future__ import annotations

import html
import os
from typing import Iterable

import requests

from .fetch import Job

TELEGRAM_API_URL = "https://api.telegram.org"
TIMEOUT = 15


def format_job_message(j: Job) -> str:
    """Format a single Job into a clean, mobile-friendly HTML message for Telegram."""
    score = j.score or 0.0
    badge = "🟢" if score >= 8.5 else "🟡" if score >= 7.0 else "⚪"
    d = j.draft or {}

    title_safe = html.escape(j.title)
    company_safe = html.escape(j.company)
    loc_safe = html.escape(j.location or "Location not stated")
    ats_safe = html.escape(j.ats.upper())
    url_safe = html.escape(j.url)

    lines = [
        f"🚀 <b>New Job Match Found!</b>",
        f"<b>{title_safe}</b> @ <b>{company_safe}</b>",
        f"📍 <i>{loc_safe}</i>  [{ats_safe}]",
        f"Fit Score: {badge} <b>{score:.1f} / 10</b>",
    ]

    fit = d.get("fit_summary") or j.reason
    if fit:
        lines.append(f"\n💡 <b>Why it fits:</b>\n{html.escape(str(fit))}")

    bullets = d.get("tailored_bullets") or []
    if bullets:
        lines.append(f"\n🎯 <b>Tailored Highlights:</b>")
        for b in bullets[:2]:
            lines.append(f"• {html.escape(str(b))}")

    cold_dm = d.get("cold_dm")
    if cold_dm:
        lines.append(f"\n💬 <b>Quick Cold DM (tap to copy):</b>\n<code>{html.escape(str(cold_dm))}</code>")

    lines.append(f'\n👉 <a href="{url_safe}"><b>Open &amp; Apply Now →</b></a>')
    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text: str,
                          disable_web_page_preview: bool = True) -> bool:
    """Send an HTML message via the Telegram Bot API."""
    url = f"{TELEGRAM_API_URL}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_web_page_preview,
    }
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        print(f"  ! Telegram notification failed ({r.status_code}): {r.text[:200]}")
        return False
    except requests.RequestException as e:
        print(f"  ! Telegram connection error: {e}")
        return False


def notify_jobs(jobs: Iterable[Job], token: str | None = None,
                chat_id: str | None = None, threshold: float = 7.0) -> int:
    """Send individual notifications for each shortlisted job."""
    bot_token = (token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    target_chat = (chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not target_chat:
        return 0

    # Only alert on high-fit roles that clear the score threshold
    job_list = [j for j in jobs if (j.score or 0.0) >= threshold]
    if not job_list:
        return 0

    print(f"\n[telegram] sending alerts for {len(job_list)} job(s)...")
    sent_count = 0
    for j in job_list:
        msg = format_job_message(j)
        if send_telegram_message(bot_token, target_chat, msg):
            sent_count += 1
            print(f"  ✓ sent Telegram alert for {j.title} @ {j.company}")

    return sent_count
