"""Mailer address parsing. No SMTP."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt.mailer import split_addrs


def test_split_addrs_comma_and_semicolon():
    assert split_addrs("a@x.com, b@y.com; c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]


def test_split_addrs_dedupes_and_drops_blanks():
    assert split_addrs(" a@x.com, a@x.com, , ") == ["a@x.com"]
    assert split_addrs("") == []
    assert split_addrs(None) == []
