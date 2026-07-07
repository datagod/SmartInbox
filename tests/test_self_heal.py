"""Self-heal detection helpers."""

from __future__ import annotations

import time
from collections import deque

from smartinbox.core import SmartInboxCore


def _core() -> SmartInboxCore:
    return SmartInboxCore({"self_heal": {"enabled": True}})


def test_is_healable_component_error_matches_timeout():
    assert SmartInboxCore._is_healable_component_error("Chatterbox TTS timed out after 120s")


def test_recent_llm_failures_counts_recent_errors():
    core = _core()
    now = time.time()
    samples = core._llm_timing_samples["summary"]
    samples.append({"ts": now - 30, "duration": 120.0, "success": False})
    samples.append({"ts": now - 10, "duration": 120.0, "success": False})
    assert core._recent_llm_failures(within_sec=600, min_failures=2) == {"summary": 2}


def test_self_heal_action_cooldown():
    core = _core()
    assert core._self_heal_action_ready("tts_restart", 60.0)
    core._mark_self_heal_action("tts_restart")
    assert not core._self_heal_action_ready("tts_restart", 60.0)


def test_record_tts_failure_tracks_samples():
    core = _core()
    core._record_tts_failure("connection refused")
    assert len(core._recent_tts_failures(within_sec=60)) == 1