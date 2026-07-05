"""Calendar LLM extraction post-filter tests."""

from smartinbox.calendar_extract import _source_has_explicit_time


def test_source_has_explicit_time_accepts_iso_datetime():
    assert _source_has_explicit_time("2026-06-21 19:05:00")


def test_source_has_explicit_time_accepts_24_hour_clock():
    assert _source_has_explicit_time("Meeting at 14:00 in the boardroom")


def test_source_has_explicit_time_rejects_date_only():
    assert not _source_has_explicit_time("June 27, 2026 in Ottawa")