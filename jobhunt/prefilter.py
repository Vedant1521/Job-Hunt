"""Deterministic filter that runs BEFORE any LLM call.

This is the whole cost story: ~2000 raw jobs -> ~40 candidates for ~0 rupees,
so Claude only ever reads jobs that already passed title + location + freshness.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .fetch import Job

REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh")

# Remote postings pass only when they are India-eligible, not Remote (US) / (FR).
INDIA_REMOTE_RE = re.compile(
    r"\b(india|indian|bharat)\b"
    r"|\b(bengaluru|bangalore|hyderabad|new\s*delhi|delhi|noida|"
    r"gurgaon|gurugram|pune|mumbai|chennai|kolkata)\b"
    r"|\bremote\s*[\-–—/,(]\s*in\b"
    r"|\(\s*in\s*\)",
    re.I,
)


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_remote(location: str, title: str) -> bool:
    loc = (location or "").lower()
    if any(h in loc for h in REMOTE_HINTS):
        return True
    return bool(re.search(r"\b(remote|work from home|\bwfh\b)\b", title or "", re.I))


def _is_india_remote(text: str) -> bool:
    return bool(INDIA_REMOTE_RE.search(text))


def prefilter(jobs: list[Job], cfg: dict) -> list[Job]:
    inc = cfg.get("include_titles") or [r"."]
    exc = cfg.get("exclude_titles") or []
    locs = [l.lower() for l in (cfg.get("locations") or [])]
    allow_remote = bool(cfg.get("allow_remote", True))
    max_age = cfg.get("max_age_days")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None

    kept, stats = [], {"title": 0, "location": 0, "age": 0}
    for j in jobs:
        if not _any_match(inc, j.title) or (exc and _any_match(exc, j.title)):
            stats["title"] += 1
            continue

        hay = f"{j.location} {j.title}".lower()
        if _is_remote(j.location, j.title):
            if not (allow_remote and _is_india_remote(hay)):
                stats["location"] += 1
                continue
        elif locs and not any(l in hay for l in locs):
            stats["location"] += 1
            continue

        if cutoff:
            posted = _parse_date(j.posted_at)
            if posted and posted < cutoff:
                stats["age"] += 1
                continue

        kept.append(j)

    print(f"  prefilter: {len(jobs)} -> {len(kept)} "
          f"(dropped title={stats['title']} location={stats['location']} stale={stats['age']})")
    return kept
