"""Phrase recording retention tests."""

from __future__ import annotations

import os
from pathlib import Path

from smartinbox.tts_recording_cache import (
    DEFAULT_PHRASE_RECORDING_RETENTION_HOURS,
    normalize_phrase_recording_retention_hours,
    phrase_recording_is_fresh,
    prune_expired_phrase_recordings,
)


def test_normalize_phrase_recording_retention_hours_defaults():
    assert normalize_phrase_recording_retention_hours(None) == DEFAULT_PHRASE_RECORDING_RETENTION_HOURS
    assert normalize_phrase_recording_retention_hours("bad") == DEFAULT_PHRASE_RECORDING_RETENTION_HOURS
    assert normalize_phrase_recording_retention_hours(24) == 24.0
    assert normalize_phrase_recording_retention_hours(0) == 0.0
    assert normalize_phrase_recording_retention_hours(9999) == 720.0


def test_phrase_recording_is_fresh(tmp_path: Path):
    path = tmp_path / "sample.wav"
    path.write_bytes(b"wav")
    now = 1_000_000.0
    assert phrase_recording_is_fresh(path, retention_hours=24, now=now)
    old = now - (25 * 3600)
    os.utime(path, (old, old))
    assert not phrase_recording_is_fresh(path, retention_hours=24, now=now)
    assert phrase_recording_is_fresh(path, retention_hours=0, now=now)


def test_prune_expired_phrase_recordings(tmp_path: Path):
    phrases = tmp_path / "phrases"
    phrases.mkdir()
    fresh = phrases / "fresh.wav"
    stale = phrases / "stale.wav"
    fresh.write_bytes(b"a")
    stale.write_bytes(b"b")
    now = 2_000_000.0
    os.utime(stale, (now - 48 * 3600, now - 48 * 3600))
    os.utime(fresh, (now - 3600, now - 3600))

    result = prune_expired_phrase_recordings(
        str(tmp_path),
        retention_hours=24,
        now=now,
    )
    assert result["skipped"] is False
    assert "stale.wav" in result["deleted"]
    assert fresh.is_file()
    assert not stale.is_file()

    keep_forever = prune_expired_phrase_recordings(str(tmp_path), retention_hours=0, now=now)
    assert keep_forever["skipped"] is True