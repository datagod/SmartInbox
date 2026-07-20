"""Tests for possible Indian middleman detection."""

from __future__ import annotations

from smartinbox.email_middleman import (
    TAG_POSSIBLE_INDIAN_MIDDLEMAN,
    classify_possible_indian_middleman,
    parse_email_tags,
    serialize_email_tags,
    tags_for_email,
)


def test_detects_classic_c2c_bench_sales_pitch():
    result = classify_possible_indian_middleman(
        sender="Rajesh Kumar <rajesh.kumar.bench@gmail.com>",
        subject="Urgent C2C requirement — Canada client",
        body=(
            "I came across your profile on LinkedIn. My client in Toronto has an urgent "
            "C2C requirement. We are a preferred vendor based out of India working with "
            "US and Canada end clients. Please share your updated resume and bill rate."
        ),
    )
    assert result["matched"] is True
    assert TAG_POSSIBLE_INDIAN_MIDDLEMAN in result["tags"]
    assert result["recruiting_score"] >= 3
    assert any("surname" in r or "given" in r for r in result["reasons"])


def test_name_alone_is_not_enough():
    result = classify_possible_indian_middleman(
        sender="Priya Sharma <priya.sharma@example.com>",
        subject="Dinner plans this weekend?",
        body="Want to grab dinner on Saturday? No work talk.",
    )
    assert result["matched"] is False
    assert result["tags"] == []


def test_recruiting_without_name_or_geo_not_enough():
    result = classify_possible_indian_middleman(
        sender="Alex Johnson <alex@staffing-us.example>",
        subject="Role available",
        body="We have a contract opportunity. Are you open for new roles?",
    )
    assert result["matched"] is False


def test_strong_middleman_language_with_geo():
    result = classify_possible_indian_middleman(
        sender="Talent Desk <desk@vendor.example>",
        subject="Hot requirement — corp to corp",
        body=(
            "Bench sales team here. End client in the USA needs consultants. "
            "C2C only. Implementation partner for multi-vendor layers. "
            "Based in Bangalore, India."
        ),
    )
    assert result["matched"] is True


def test_tags_for_email_merges_idempotently():
    tags = tags_for_email(
        sender="Amit Patel <amit.patel@yahoo.com>",
        subject="C2C / W2 openings",
        body="My client is looking for resources. Share your CV. Client in Canada.",
        existing_tags=["possible_indian_middleman", "other_tag"],
    )
    assert "possible_indian_middleman" in tags
    assert "other_tag" in tags
    # Re-run without match should drop middleman but keep other
    tags2 = tags_for_email(
        sender="Bob <bob@example.com>",
        subject="Hello",
        body="Just saying hi",
        existing_tags=tags,
    )
    assert "possible_indian_middleman" not in tags2
    assert "other_tag" in tags2


def test_parse_and_serialize_tags_roundtrip():
    raw = serialize_email_tags(["possible_indian_middleman", "z", "a"])
    assert parse_email_tags(raw) == ["a", "possible_indian_middleman", "z"]
    assert parse_email_tags(None) == []
    assert parse_email_tags("possible_indian_middleman") == ["possible_indian_middleman"]


def test_activity_log_messages_include_sender_score_and_reasons():
    from smartinbox.email_middleman import format_middleman_activity_messages

    result = classify_possible_indian_middleman(
        sender="Rajesh Kumar <rajesh.kumar.bench@gmail.com>",
        subject="Urgent C2C requirement — Canada client",
        body=(
            "I came across your profile on LinkedIn. My client in Toronto has an urgent "
            "C2C requirement. We are a preferred vendor based out of India. "
            "Please share your updated resume and bill rate."
        ),
    )
    lines = format_middleman_activity_messages(
        result,
        subject="Urgent C2C requirement — Canada client",
        sender="Rajesh Kumar <rajesh.kumar.bench@gmail.com>",
        context="tagged",
        source="new mail",
    )
    assert lines
    assert "Middleman check" in lines[0]
    assert "FLAGGED" in lines[0]
    assert "new mail" in lines[0]
    assert any(line.strip().startswith("Sender:") for line in lines)
    assert any("Score" in line and "recruiting" in line for line in lines)
    assert any("C2C" in line or "Profile:" in line for line in lines)


def test_activity_log_cleared_context():
    from smartinbox.email_middleman import format_middleman_activity_messages

    result = classify_possible_indian_middleman(
        sender="Bob <bob@example.com>",
        subject="Hello",
        body="Just saying hi",
    )
    lines = format_middleman_activity_messages(
        result,
        subject="Hello",
        sender="Bob <bob@example.com>",
        context="cleared",
    )
    assert any("cleared middleman tag" in line for line in lines)


def test_activity_log_checked_not_flagged_always_has_detail():
    from smartinbox.email_middleman import format_middleman_activity_messages

    result = classify_possible_indian_middleman(
        sender="Bob <bob@example.com>",
        subject="Hello",
        body="Just saying hi",
    )
    lines = format_middleman_activity_messages(
        result,
        subject="Hello",
        sender="Bob <bob@example.com>",
        context="checked",
        source="new mail",
    )
    assert any("not flagged" in line for line in lines)
    assert any("new mail" in line for line in lines)
    assert any("Sender:" in line for line in lines)
    assert any("No recruiting" in line or "Weak signals" in line for line in lines)


def test_confirmed_tag_overrides_possible():
    from smartinbox.email_middleman import TAG_INDIAN_MIDDLEMAN, TAG_POSSIBLE_INDIAN_MIDDLEMAN

    tags = tags_for_email(
        sender="Rajesh Kumar <rajesh@vendor-co.example>",
        subject="Urgent C2C",
        body="My client needs C2C. Based in India. Share resume.",
        confirmed=True,
    )
    assert TAG_INDIAN_MIDDLEMAN in tags
    assert TAG_POSSIBLE_INDIAN_MIDDLEMAN not in tags


def test_domain_auto_tag_blocked_for_gmail():
    from smartinbox.email_middleman import domain_auto_tag_allowed, extract_sender_domain

    assert extract_sender_domain("Raj <raj@Gmail.Com>") == "gmail.com"
    assert domain_auto_tag_allowed("gmail.com") is False
    assert domain_auto_tag_allowed("acme-staffing.com") is True


def test_foreign_middleman_eteam_us_nj_address_no_zip():
    """eTeam-style: US NJ signature without ZIP + Toronto on-site DBA role."""
    from smartinbox.email_middleman import classify_foreign_middleman, tags_for_email

    sender = "Supriya Mokashi <smokashi@eteaminc.com>"
    subject = "Hiring Database Administrator opportunity – quick check"
    body = (
        "My name is Supriya, and I'm a Senior Technical Recruiter with eTeam Inc. "
        "I came across your profile through a job board where your resume is publicly "
        "available. Role Name: Database Administrator. "
        "Location: Toronto 4 days a week on site F2F interview required. "
        "Please share updated resume.\n"
        "Supriya Mokashi, Senior Technical Recruiter, eTeam Inc.\n"
        "(732) 352-3045 | smokashi@eteaminc.com\n"
        "285 Davidson Avenue Suite 406, Somerset, NJ, Somerset\n"
    )
    result = classify_foreign_middleman(sender=sender, subject=subject, body=body)
    assert result["matched"] is True, result
    assert result["country_code"] == "US", result
    assert "foreign_middleman_us" in result["tags"]
    assert "foreign_middleman_us" in tags_for_email(
        sender=sender, subject=subject, body=body
    )


def test_foreign_middleman_bravens_us_staffing_canada_job():
    """Real-world pattern: US staffing firm pitching a Toronto on-site role."""
    from smartinbox.email_middleman import classify_foreign_middleman, tags_for_email

    sender = "Yogita Gosavi <yogita.gosavi@bravensinc.com>"
    subject = (
        "Database Administrator – Toronto, ON "
        "(4 days a week on site and F2F interview required)"
    )
    body = (
        "Hi William,\n"
        "Bravens Inc's (Ampcus Group of Companies – Ampcus Inc., Bravens Inc., "
        "iTech Solutions) is a Certified Minority Owned Global technology and "
        "business consulting organization that provides Human Capital services "
        "to over 100 Fortune 500 businesses as a preferred vendor.\n"
        "We are currently looking for an Database Administrator – Toronto, ON "
        "(4 days a week on site and F2F interview required).\n"
        "Title: Database Administrator\n"
        "Location: Toronto, ON (4 days a week on site and F2F interview required)\n"
        "Yogita Gosavi, Sr. Specialist – Talent Acquisition\n"
        "+1-703-291-4860\n"
        "Email ID : yogita.gosavi@bravensinc.com  Website: www.bravensinc.com\n"
        "14900 Conference Center Dr., Suite 500, Chantilly, VA 20151.\n"
        "USA | Canada | Singapore | Philippines | India | Dubai\n"
    )
    result = classify_foreign_middleman(sender=sender, subject=subject, body=body)
    assert result["matched"] is True, result
    assert result["country_code"] == "US", result
    assert result["country_flag"] == "🇺🇸"
    assert "foreign_middleman_us" in result["tags"]
    tags = tags_for_email(sender=sender, subject=subject, body=body)
    assert "foreign_middleman_us" in tags


def test_foreign_middleman_canada_job_foreign_company():
    from smartinbox.email_middleman import (
        classify_foreign_middleman,
        tag_label,
        tags_for_email,
    )

    result = classify_foreign_middleman(
        sender="Priya Nair <priya@talentdesk.co.in>",
        subject="Contract Database Administrator || Toronto, ON",
        body=(
            "We have a C2C contract opportunity in Toronto, Canada. "
            "Our staffing firm is based out of India (Hyderabad office). "
            "Please share your updated resume and bill rate."
        ),
    )
    assert result["matched"] is True
    assert result["country_code"] == "IN"
    assert result["country_flag"] == "🇮🇳"
    assert "foreign_middleman_in" in result["tags"]
    assert "🇮🇳" in tag_label("foreign_middleman_in")

    tags = tags_for_email(
        sender="Priya Nair <priya@talentdesk.co.in>",
        subject="Contract Database Administrator || Toronto, ON",
        body=(
            "We have a C2C contract opportunity in Toronto, Canada. "
            "Our staffing firm is based out of India (Hyderabad office). "
            "Please share your updated resume and bill rate."
        ),
    )
    assert any(t.startswith("foreign_middleman") for t in tags)


def test_foreign_middleman_skips_canadian_company():
    from smartinbox.email_middleman import classify_foreign_middleman

    result = classify_foreign_middleman(
        sender="HR <jobs@acme.ca>",
        subject="Full-time engineer — Toronto",
        body="Full-time role in Toronto, Canada. Headquartered in Canada.",
    )
    assert result["matched"] is False


def test_foreign_middleman_needs_canada_job():
    from smartinbox.email_middleman import classify_foreign_middleman

    result = classify_foreign_middleman(
        sender="Desk <desk@vendor.co.in>",
        subject="C2C role Texas USA",
        body="C2C requirement in Dallas, Texas. Based out of India.",
    )
    assert result["matched"] is False


def test_confirm_middleman_persists_and_fans_out_domain(tmp_path):
    import sqlite3

    from smartinbox.db import init_db, update_email_tags, upsert_email
    from smartinbox.email_middleman import (
        TAG_INDIAN_MIDDLEMAN,
        TAG_POSSIBLE_INDIAN_MIDDLEMAN,
        add_confirmed_indian_middleman,
        emails_matching_middleman_scope,
        is_confirmed_middleman_sender,
        parse_email_tags,
        promote_tags_to_confirmed,
    )

    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    upsert_email(
        conn,
        {
            "id": "e1",
            "sender": "Rajesh Kumar <rajesh@staffing-co.example>",
            "subject": "C2C",
            "body_text": "C2C my client India",
            "received_at": 1.0,
        },
    )
    upsert_email(
        conn,
        {
            "id": "e2",
            "sender": "Other Rep <other@staffing-co.example>",
            "subject": "Role",
            "body_text": "hi",
            "received_at": 2.0,
        },
    )
    upsert_email(
        conn,
        {
            "id": "e3",
            "sender": "Unrelated <x@other.com>",
            "subject": "Hi",
            "body_text": "hi",
            "received_at": 3.0,
        },
    )
    update_email_tags(conn, "e1", [TAG_POSSIBLE_INDIAN_MIDDLEMAN])

    entry = add_confirmed_indian_middleman(
        conn,
        sender="Rajesh Kumar <rajesh@staffing-co.example>",
        source_email_id="e1",
        source_subject="C2C",
    )
    assert entry["email_address"] == "rajesh@staffing-co.example"
    assert int(entry["auto_domain"]) == 1
    assert is_confirmed_middleman_sender(
        conn, "Other Rep <other@staffing-co.example>"
    )

    matches = emails_matching_middleman_scope(
        conn,
        sender_key=entry["sender_key"],
        domain=entry["domain"],
        auto_domain=True,
    )
    ids = {m["id"] for m in matches}
    assert "e1" in ids and "e2" in ids
    assert "e3" not in ids

    for m in matches:
        update_email_tags(
            conn, m["id"], promote_tags_to_confirmed(parse_email_tags(m.get("tags")))
        )
    row = conn.execute("SELECT tags FROM emails WHERE id = 'e1'").fetchone()
    assert TAG_INDIAN_MIDDLEMAN in parse_email_tags(row["tags"])
    assert TAG_POSSIBLE_INDIAN_MIDDLEMAN not in parse_email_tags(row["tags"])


def test_confirm_foreign_middleman_persists_and_fans_out(tmp_path):
    import sqlite3

    from smartinbox.db import init_db, update_email_tags, upsert_email
    from smartinbox.email_middleman import (
        add_confirmed_foreign_middleman,
        confirmed_tags_for_sender,
        emails_matching_middleman_scope,
        is_confirmed_middleman_sender,
        parse_email_tags,
        promote_tags_to_foreign,
    )

    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    upsert_email(
        conn,
        {
            "id": "f1",
            "sender": "Yogita Gosavi <yogita.gosavi@bravensinc.com>",
            "subject": "DBA Toronto",
            "body_text": "Toronto job",
            "received_at": 1.0,
        },
    )
    upsert_email(
        conn,
        {
            "id": "f2",
            "sender": "Other <desk@bravensinc.com>",
            "subject": "Role",
            "body_text": "hi",
            "received_at": 2.0,
        },
    )
    update_email_tags(conn, "f1", ["foreign_middleman_us"])

    entry = add_confirmed_foreign_middleman(
        conn,
        sender="Yogita Gosavi <yogita.gosavi@bravensinc.com>",
        country_code="US",
        tag_id="foreign_middleman_us",
        source_email_id="f1",
        source_subject="DBA Toronto",
    )
    assert entry["kind"] == "foreign"
    assert entry["country_code"] == "US"
    assert entry["tag_id"] == "foreign_middleman_us"
    assert int(entry["auto_domain"]) == 1
    assert is_confirmed_middleman_sender(conn, "desk@bravensinc.com")
    assert confirmed_tags_for_sender(conn, "Other <desk@bravensinc.com>") == [
        "foreign_middleman_us"
    ]

    matches = emails_matching_middleman_scope(
        conn,
        sender_key=entry["sender_key"],
        domain=entry["domain"],
        auto_domain=True,
    )
    assert {m["id"] for m in matches} >= {"f1", "f2"}
    for m in matches:
        update_email_tags(
            conn,
            m["id"],
            promote_tags_to_foreign(
                parse_email_tags(m.get("tags")), tag_id="foreign_middleman_us"
            ),
        )
    row = conn.execute("SELECT tags FROM emails WHERE id = 'f2'").fetchone()
    assert "foreign_middleman_us" in parse_email_tags(row["tags"])
