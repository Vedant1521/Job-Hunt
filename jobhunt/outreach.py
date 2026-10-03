"""Daily YC job search: Work-at-a-Startup listings scored against the resume.

This is the second email. It does not scrape Greenhouse/Lever/Ashby (that's
`jobhunt run`) and it does not email founders. It reads public YC JSON, keeps
engineering/intern/new-grad roles that match the profile, and drafts an
application kit. On-site can be anywhere; remote must be Remote-India.

Sources:
  https://yc-oss.github.io/api/tags/{ai,developer-tools}.json
  https://devasheeshg.github.io/yc-api/batches/{batch}/{slug}.json  (jobs[])
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

from .fetch import Job, UA
from .llm import draft, keyword_screen, screen
from .prefilter import prefilter
from .providers import LLMError, resolve

TIMEOUT = 20
YC_OSS = "https://yc-oss.github.io/api"
YC_API = "https://devasheeshg.github.io/yc-api"
MAX_WORKERS = 8

FIT_RE = re.compile(
    r"\b(ai|llm|agent|agentic|rag|developer|devtools|saas|workflow|"
    r"orchestrat|api|cloud|infra|full.?stack|next\.?js|typescript|"
    r"python|gcp|open.?source|document|automation|mlops|observab|"
    r"crypto|web3|solana|blockchain|"
    r"software|engineer|intern)\b",
    re.I,
)


class OutreachStore:
    """Dedupe so the same YC job is not emailed every morning."""

    def __init__(self, path: str | Path = "seen_outreach.json"):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"  ! {self.path} corrupt, starting fresh")

    def unseen(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if j.job_id not in self.data]

    def record(self, jobs: list[Job], emailed: bool) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for j in jobs:
            self.data.setdefault(j.job_id, {
                "first_seen": now,
                "company": j.company,
                "title": j.title,
                "url": j.url,
                "score": j.score,
                "emailed": emailed,
            })
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False),
                             encoding="utf-8")


def batch_slug(batch: str) -> str:
    return (batch or "").strip().lower().replace(" ", "-")


def _get_json(session: requests.Session, url: str) -> Any | None:
    try:
        r = session.get(url, timeout=TIMEOUT, headers=UA)
    except requests.RequestException as e:
        print(f"  ! {url} ({type(e).__name__})", flush=True)
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def keyword_score(company: dict, profile: dict | None = None) -> float:
    """Rank YC companies before fetching their job lists. No location bias."""
    blob = " ".join([
        company.get("name") or "",
        company.get("one_liner") or "",
        (company.get("long_description") or "")[:800],
        " ".join(company.get("tags") or []),
        " ".join(company.get("industries") or []),
    ]).lower()
    score = 0.0
    if FIT_RE.search(blob):
        score += 3.0
    if company.get("isHiring") or company.get("hiring"):
        score += 2.0
    year_m = re.search(r"(20\d{2})", company.get("batch") or "")
    year = int(year_m.group(1)) if year_m else 0
    if year >= 2026:
        score += 1.5
    elif year >= 2025:
        score += 1.0
    skills = [s.lower() for s in (profile or {}).get("core_skills", []) if s]
    domains = [d.lower() for d in (profile or {}).get("domains", []) if d]
    titles = [t.lower() for t in (profile or {}).get("target_titles", []) if t]
    for token in skills + domains + titles:
        if len(token) >= 4 and token in blob:
            score += 0.4
    return score


def eligible(company: dict) -> bool:
    status = (company.get("status") or "Active").lower()
    if status in ("inactive", "acquired", "public"):
        return False
    if not (company.get("isHiring") or company.get("hiring") or company.get("_extra")):
        return False
    blob = " ".join([
        company.get("name") or "",
        company.get("one_liner") or "",
        " ".join(company.get("tags") or []),
        " ".join(company.get("industries") or []),
    ])
    return bool(FIT_RE.search(blob) or company.get("_extra"))


def load_extra(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for c in data.get("companies") or []:
        if not c.get("name"):
            continue
        slug = c.get("slug") or re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
        out.append({
            "name": c["name"],
            "slug": slug,
            "website": c.get("website") or "",
            "one_liner": c.get("one_liner") or "",
            "all_locations": c.get("location") or "",
            "tags": list(c.get("tags") or []),
            "isHiring": True,
            "hiring": True,
            "status": "Active",
            "batch": c.get("batch") or "",
            "url": c.get("url") or "",
            "_extra": True,
        })
    return out


def fetch_companies(session: requests.Session, extra_file: str | Path) -> list[dict]:
    pool: dict[str, dict] = {}

    def add_all(rows: list | None, src: str) -> None:
        for c in rows or []:
            slug = c.get("slug")
            if not slug:
                continue
            c["_src"] = src
            prev = pool.get(slug)
            if prev is None:
                pool[slug] = c
                continue
            if c.get("_extra"):
                prev["isHiring"] = prev["hiring"] = True
                prev["_extra"] = True
                if c.get("batch") and not prev.get("batch"):
                    prev["batch"] = c["batch"]
                continue
            if c.get("isHiring") and not prev.get("isHiring"):
                pool[slug] = c

    for tag in ("ai", "developer-tools", "fintech", "infrastructure", "saas"):
        add_all(_get_json(session, f"{YC_OSS}/tags/{tag}.json"), tag)
    add_all(load_extra(extra_file), "extra")
    return list(pool.values())


def job_from_yc(company: dict, posting: dict) -> Job:
    skills = ", ".join(posting.get("skills") or [])
    bits = [
        company.get("one_liner") or "",
        f"YC {company.get('batch') or ''}".strip(),
        posting.get("role") or "",
        posting.get("role_type") or "",
        posting.get("type") or "",
        posting.get("experience") or "",
        posting.get("visa") or "",
        f"Skills: {skills}" if skills else "",
        (company.get("long_description") or "")[:900],
    ]
    return Job(
        job_id=f"yc:{company.get('slug')}:{posting.get('id')}",
        ats="yc",
        company=company.get("name") or company.get("slug") or "YC",
        title=(posting.get("title") or "").strip(),
        location=(posting.get("location") or company.get("all_locations") or "").strip(),
        url=posting.get("url") or company.get("url") or "",
        description="\n".join(b for b in bits if b),
        salary=posting.get("salary_range") or None,
    )


def fetch_company_jobs(company: dict) -> list[Job]:
    slug = company.get("slug")
    batch = batch_slug(company.get("batch") or "")
    if not slug or not batch:
        return []
    session = requests.Session()
    detail = _get_json(session, f"{YC_API}/batches/{batch}/{slug}.json")
    postings = (detail or {}).get("jobs") or []
    return [job_from_yc(company, p) for p in postings if p.get("id") and p.get("title")]


def job_boost(job: Job, profile: dict) -> float:
    """Prefer intern / new-grad / FDE / AI titles that match the resume."""
    blob = f"{job.title} {job.description}".lower()
    score = 0.0
    if re.search(r"\bintern(ship)?\b", blob):
        score += 3.0
    if re.search(r"new\s*grad|university|graduate", blob):
        score += 2.5
    if "any (new grads ok)" in blob:
        score += 2.0
    if re.search(r"forward deployed|\bfde\b|applied ai", blob):
        score += 2.0
    if re.search(r"\b(full.?stack|software engineer|ai engineer)\b", blob):
        score += 1.0
    for skill in (profile or {}).get("core_skills") or []:
        if skill and len(skill) >= 4 and skill.lower() in blob:
            score += 0.3
    return score


MOCK_JOBS = [
    Job(job_id="yc:riverlane-ai:1", ats="yc", company="Riverlane AI",
        title="Software Engineer Intern", location="San Francisco, CA",
        url="https://www.ycombinator.com/companies/riverlane-ai/jobs/1",
        description="YC Winter 2026. Agents for document workflows. TypeScript, Python, LangChain. Any (new grads ok)."),
    Job(job_id="yc:northbeam-tools:2", ats="yc", company="Northbeam Tools",
        title="Full Stack Engineer", location="Remote - India",
        url="https://www.ycombinator.com/companies/northbeam-tools/jobs/2",
        description="YC Summer 2025. Developer tools for Next.js teams. TypeScript, Python, Next.js."),
]


def collect(profile: dict, cfg: dict, *, mock: bool = False,
            limit: int | None = None) -> tuple[list[Job], OutreachStore, int]:
    outreach_cfg = cfg.get("outreach") or {}
    max_n = int(limit or outreach_cfg.get("max_per_digest") or 5)
    max_companies = int(outreach_cfg.get("max_companies") or 60)
    extra = outreach_cfg.get("extra_file") or "outreach_extra.yaml"
    store = OutreachStore(outreach_cfg.get("seen_file") or "seen_outreach.json")
    title_filters = dict(cfg.get("filters") or {})
    # YC: on-site anywhere (empty locations list). Remote still must be India.
    title_filters["locations"] = []
    title_filters["allow_remote"] = True

    if mock:
        jobs = list(MOCK_JOBS)
    else:
        print("  fetching YC job lists (on-site anywhere, remote India)", flush=True)
        session = requests.Session()
        companies = fetch_companies(session, extra)
        ranked = [c for c in companies if eligible(c)]
        for c in ranked:
            c["_score"] = keyword_score(c, profile)
        ranked.sort(key=lambda c: c["_score"], reverse=True)
        probe = ranked[:max_companies]
        print(f"  companies {len(companies)} -> {len(ranked)} hiring/fit -> probing {len(probe)}",
              flush=True)
        jobs = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(fetch_company_jobs, c) for c in probe]
            for fut in as_completed(futs):
                try:
                    jobs.extend(fut.result())
                except Exception as e:
                    print(f"  ! jobs fetch failed ({type(e).__name__}: {e})", flush=True)
        print(f"  raw YC jobs: {len(jobs)}", flush=True)

    jobs = prefilter(jobs, title_filters)
    jobs.sort(key=lambda j: job_boost(j, profile), reverse=True)
    jobs = store.unseen(jobs)
    print(f"  new YC jobs since last run: {len(jobs)}", flush=True)
    return jobs[: max(max_n * 4, max_n)], store, max_n


def run(profile: dict, cfg: dict, *, mock: bool = False, scorer: str = "llm",
        limit: int | None = None) -> tuple[list[Job], OutreachStore]:
    jobs, store, max_n = collect(profile, cfg, mock=mock, limit=limit)
    if not jobs:
        return [], store
    threshold = float(cfg.get("score_threshold") or 7.0)
    if scorer == "keyword":
        print(f"  screening {len(jobs)} YC jobs (keyword stub)", flush=True)
        keyword_screen(jobs, profile)
    else:
        try:
            provider, model = resolve("screen")
            print(f"  screening {len(jobs)} YC jobs via {provider.name}/{model}", flush=True)
            screen(jobs, profile,
                   batch_size=int(cfg.get("screen_batch_size") or 8),
                   jd_chars=int(cfg.get("screen_jd_chars") or 1400),
                   provider=provider, model=model)
        except LLMError as e:
            print(f"  ! LLM unavailable ({e}) — keyword fallback", flush=True)
            keyword_screen(jobs, profile)

    # Any job that wasn't scored by screen (due to batch error or missing from LLM response)
    unscored = [j for j in jobs if j.score is None]
    if unscored:
        keyword_screen(unscored, profile)

    llm_scored = [
        j for j in jobs
        if j.score is not None and not (j.reason or "").startswith("[keyword stub]")
    ]
    if llm_scored:
        shortlist = sorted(
            [j for j in llm_scored if (j.score or 0) >= threshold],
            key=lambda j: ((j.score or 0) + job_boost(j, profile) * 0.15),
            reverse=True,
        )[:max_n]
        print(f"  {len(shortlist)} YC jobs >= {threshold}", flush=True)
    else:
        shortlist = sorted(
            [j for j in jobs if (j.score or 0) >= threshold],
            key=lambda j: ((j.score or 0) + job_boost(j, profile)),
            reverse=True,
        )[:max_n]
        print(f"  {len(shortlist)} YC jobs (keyword / intern-AI rank >= {threshold})", flush=True)
    if shortlist and scorer != "keyword":
        try:
            provider, model = resolve("draft")
            print(f"  drafting kits via {provider.name}/{model}", flush=True)
            draft(shortlist, profile,
                  jd_chars=int(cfg.get("draft_jd_chars") or 6000),
                  provider=provider, model=model)
        except LLMError as e:
            print(f"  ! draft unavailable ({e})", flush=True)
    return shortlist, store
