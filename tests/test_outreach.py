"""YC jobs: ranking, any-location matching, mock pipeline. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt import digest, outreach
from jobhunt.fetch import Job
from jobhunt.prefilter import prefilter


def test_us_ai_company_is_eligible_without_india_tag():
    us = {
        "name": "Langflow",
        "one_liner": "Open source LLM orchestration for developers",
        "tags": ["AI"],
        "all_locations": "San Francisco, CA",
        "isHiring": True,
        "batch": "Winter 2024",
        "status": "Active",
    }
    assert outreach.eligible(us)
    profile = {"core_skills": ["Python", "LangChain"], "domains": ["applied AI"]}
    assert outreach.keyword_score(us, profile) > 0


def test_india_tag_does_not_boost_score():
    us = {
        "name": "Nimble", "one_liner": "AI agents for document workflows",
        "tags": ["AI"], "all_locations": "San Francisco, CA",
        "isHiring": True, "batch": "Winter 2026", "status": "Active",
    }
    india = dict(us)
    india["tags"] = ["AI", "India"]
    india["all_locations"] = "Bengaluru, India"
    profile = {"core_skills": ["Python", "LangChain"], "domains": ["applied AI"]}
    assert outreach.keyword_score(us, profile) == outreach.keyword_score(india, profile)


def test_unrelated_payments_company_is_not_eligible():
    giant = {
        "name": "HugeCo", "one_liner": "Payments for enterprises",
        "tags": ["Fintech"], "all_locations": "San Francisco, CA",
        "isHiring": True, "batch": "Winter 2015", "status": "Active",
    }
    assert not outreach.eligible(giant)


def test_acquired_and_inactive_are_dropped():
    c = {"name": "Gone", "one_liner": "AI infra", "tags": ["AI"],
         "isHiring": True, "status": "Acquired", "batch": "Winter 2026"}
    assert not outreach.eligible(c)
    c["status"] = "Inactive"
    assert not outreach.eligible(c)


def test_title_prefilter_keeps_intern_drops_staff():
    jobs = [
        Job(job_id="yc:a:1", ats="yc", company="A", title="Software Engineer Intern",
            location="New York, NY", url="https://example.com/1", description="AI intern"),
        Job(job_id="yc:a:2", ats="yc", company="A", title="Staff Software Engineer",
            location="Remote", url="https://example.com/2", description="Staff AI"),
    ]
    kept = prefilter(jobs, {
        "include_titles": [r"\bintern(ship)?\b", r"software engineer"],
        "exclude_titles": [r"\b(staff|senior)\b"],
        "locations": [],
        "allow_remote": True,
    })
    assert [j.job_id for j in kept] == ["yc:a:1"]


def test_sf_onsite_kept_us_remote_dropped():
    jobs = [
        Job(job_id="yc:a:1", ats="yc", company="A", title="Software Engineer Intern",
            location="San Francisco, CA", url="https://example.com/1", description="x"),
        Job(job_id="yc:a:2", ats="yc", company="A", title="Full Stack Engineer",
            location="London, UK / Remote", url="https://example.com/2", description="x"),
        Job(job_id="yc:a:3", ats="yc", company="A", title="Full Stack Engineer",
            location="Remote (US)", url="https://example.com/3", description="x"),
        Job(job_id="yc:a:4", ats="yc", company="A", title="Full Stack Engineer",
            location="Remote - India", url="https://example.com/4", description="x"),
        Job(job_id="yc:a:5", ats="yc", company="A", title="Full Stack Engineer",
            location="Bengaluru, India / Remote", url="https://example.com/5", description="x"),
    ]
    kept = prefilter(jobs, {
        "include_titles": [r"engineer"],
        "exclude_titles": [],
        "locations": [],
        "allow_remote": True,
    })
    assert [j.job_id for j in kept] == ["yc:a:1", "yc:a:4", "yc:a:5"]


def test_store_does_not_repeat_a_job(tmp_path):
    store = outreach.OutreachStore(tmp_path / "seen.json")
    j = Job(job_id="yc:riverlane-ai:1", ats="yc", company="Riverlane AI",
            title="Software Engineer Intern", location="Remote",
            url="https://www.ycombinator.com/companies/riverlane-ai/jobs/1",
            description="x")
    store.record([j], emailed=False)
    assert store.unseen([j]) == []


def test_mock_pipeline_returns_jobs_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outreach_extra.yaml").write_text("companies: []\n")
    profile = {"name": "Talha Ansari", "current_title": "FDE Intern",
               "core_skills": ["TypeScript", "Python", "LangChain"]}
    cfg = {
        "score_threshold": 7.0,
        "outreach": {
            "seen_file": str(tmp_path / "seen_outreach.json"),
            "max_per_digest": 5,
            "max_companies": 60,
            "extra_file": str(tmp_path / "outreach_extra.yaml"),
        },
        "filters": {
            "include_titles": [r"engineer", r"intern"],
            "exclude_titles": [r"\bsenior\b"],
            "locations": ["bangalore", "india"],
            "allow_remote": True,
        },
    }
    jobs, store = outreach.run(profile, cfg, mock=True, scorer="keyword")
    assert len(jobs) == 2
    assert all(isinstance(j, Job) for j in jobs)
    assert {j.location for j in jobs} == {"San Francisco, CA", "Remote - India"}
    assert all(j.ats == "yc" for j in jobs)
    store.record(jobs, emailed=False)
    again, _ = outreach.run(profile, cfg, mock=True, scorer="keyword")
    assert again == []


def test_digest_renders_yc_job_cards():
    j = Job(
        job_id="yc:riverlane-ai:1", ats="yc", company="Riverlane AI",
        title="Software Engineer Intern", location="San Francisco, CA / Remote",
        url="https://www.ycombinator.com/companies/riverlane-ai/jobs/1",
        description="Agents for document workflows.",
        score=8.5, reason="Intern + TypeScript/Python overlap.",
        draft={"cover_note": "Hi — applying for the intern role.",
               "fit_summary": "Document AI overlap."},
    )
    subject, html = digest.build_outreach([j])
    assert "yc job" in subject.lower()
    assert "founder" not in subject.lower()
    assert "Software Engineer Intern" in html
    assert "Riverlane AI" in html
    assert "https://www.ycombinator.com/companies/riverlane-ai/jobs/1" in html
    assert "Open &amp; apply" in html
    jobs_subject, jobs_html = digest.build([], 0, 0, {"tracked": 0, "applied": 0})
    assert "Riverlane AI" not in jobs_html
    assert "yc job" not in jobs_subject.lower()
