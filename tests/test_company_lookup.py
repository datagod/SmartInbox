"""Tests for company web lookup helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from smartinbox.company_lookup import (
    apply_web_country_to_foreign_result,
    get_cached_lookup,
    html_to_text,
    infer_country_from_text,
    init_company_lookup_table,
    save_lookup,
    should_lookup_company,
)
from smartinbox.email_middleman import classify_foreign_middleman


def test_html_to_text_strips_tags():
    raw = "<html><script>x</script><body><h1>Hello</h1><p>World</p></body></html>"
    text = html_to_text(raw)
    assert "Hello" in text and "World" in text
    assert "<" not in text


def test_infer_country_from_headquarters_line():
    text = (
        "About Us. Our company is headquartered in Hyderabad, India "
        "and serves clients worldwide."
    )
    cc, evidence, conf = infer_country_from_text(text)
    assert cc == "IN"
    assert conf >= 0.8
    assert "Hyderabad" in evidence or "India" in evidence


def test_should_lookup_for_canada_job_foreign_domain():
    foreign = classify_foreign_middleman(
        sender="Rep <rep@staffing.co.in>",
        subject="Contract role Toronto Canada",
        body="C2C contract in Toronto, Canada.",
    )
    # May or may not match locally; should still allow lookup if canada job
    foreign["matched"] = False
    foreign["foreign_score"] = 0
    assert should_lookup_company(
        sender="Rep <rep@staffing.co.in>",
        foreign_result=foreign,
    )


def test_should_not_lookup_when_already_high_confidence():
    foreign = {
        "canada_job": True,
        "job_or_contract": True,
        "matched": True,
        "foreign_score": 8,
    }
    assert (
        should_lookup_company(
            sender="Rep <rep@staffing.co.in>",
            foreign_result=foreign,
        )
        is False
    )


def test_apply_web_country_flags_foreign_middleman():
    foreign = classify_foreign_middleman(
        sender="Rep <rep@vendor.example>",
        subject="Contract DBA Toronto, ON Canada",
        body="C2C contract opportunity in Toronto, Canada. Share resume.",
    )
    # Local heuristics may not know vendor.example country
    foreign["matched"] = False
    foreign["tags"] = []
    updated = apply_web_country_to_foreign_result(
        foreign,
        country_code="IN",
        confidence=0.9,
        source="https://vendor.example/about",
        evidence="headquartered in India",
    )
    assert updated["matched"] is True
    assert updated["country_code"] == "IN"
    assert updated["country_flag"] == "🇮🇳"
    assert any(t.startswith("foreign_middleman") for t in updated["tags"])


def test_apply_web_country_canada_clears_foreign():
    foreign = {
        "canada_job": True,
        "job_or_contract": True,
        "matched": True,
        "tags": ["foreign_middleman_us"],
        "foreign_score": 4,
    }
    updated = apply_web_country_to_foreign_result(
        foreign,
        country_code="CA",
        confidence=0.9,
        source="https://acme.ca/about",
        evidence="headquartered in Canada",
    )
    assert updated["matched"] is False
    assert updated["tags"] == []


def test_lookup_cache_roundtrip(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "c.db"))
    conn.row_factory = sqlite3.Row
    init_company_lookup_table(conn)
    save_lookup(
        conn,
        domain="staffing.example",
        company_name=None,
        country_code="PH",
        source="test",
        evidence="Manila office",
        confidence=0.8,
    )
    cached = get_cached_lookup(conn, domain="staffing.example")
    assert cached is not None
    assert cached["country_code"] == "PH"
    assert cached["country_flag"] == "🇵🇭"
