"""Test Telegram notification formatting and dispatch."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt.fetch import Job
from jobhunt.notifier import format_job_message, notify_jobs, send_telegram_message


def test_format_job_message_includes_all_fields():
    job = Job(
        job_id="greenhouse:stripe:101",
        ats="greenhouse",
        company="Stripe",
        title="Software Engineering Intern",
        location="Bengaluru, India",
        url="https://boards.greenhouse.io/stripe/jobs/101",
        description="Core payments platform.",
        score=9.0,
        reason="Perfect fit for backend and distributed systems.",
        draft={
            "fit_summary": "Strong match with Python & Redis.",
            "tailored_bullets": ["Built Solana payment gateway with sub-200ms routing."],
            "cold_dm": "Hey! Saw the SWE intern role at Stripe. Built Botlock and NexusAI.",
        },
    )
    msg = format_job_message(job)
    assert "Software Engineering Intern" in msg
    assert "Stripe" in msg
    assert "Bengaluru, India" in msg
    assert "9.0 / 10" in msg
    assert "Strong match with Python" in msg
    assert "Built Solana payment gateway" in msg
    assert "Hey! Saw the SWE intern role" in msg
    assert "https://boards.greenhouse.io/stripe/jobs/101" in msg


def test_send_telegram_message_success():
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        ok = send_telegram_message("test_token", "12345", "<b>Hello</b>")
        assert ok is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "bottest_token/sendMessage" in args[0]
        assert kwargs["json"]["chat_id"] == "12345"
        assert kwargs["json"]["text"] == "<b>Hello</b>"
        assert kwargs["json"]["parse_mode"] == "HTML"


def test_notify_jobs_skips_when_no_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    job = Job(
        job_id="greenhouse:stripe:101",
        ats="greenhouse",
        company="Stripe",
        title="Software Engineering Intern",
        location="Bengaluru",
        url="https://example.com",
        description="x",
    )
    sent = notify_jobs([job])
    assert sent == 0


def test_notify_jobs_success():
    job = Job(
        job_id="greenhouse:stripe:101",
        ats="greenhouse",
        company="Stripe",
        title="Software Engineering Intern",
        location="Bengaluru",
        url="https://example.com",
        description="x",
        score=8.5,
    )
    with patch("jobhunt.notifier.send_telegram_message", return_value=True) as mock_send:
        sent = notify_jobs([job], token="fake_token", chat_id="12345", threshold=7.0)
        assert sent == 1
        mock_send.assert_called_once()

