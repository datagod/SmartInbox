# SmartInbox Code Review

**Scope:** Uncommitted changes (14 files, +1074 / −143 lines)  
**Date:** 2026-07-04  
**Tests:** `pytest tests/` — 6/6 passed

## Summary

This changeset delivers four cohesive features:

1. **Stricter calendar time extraction** — no more defaulting to 09:00; dates without explicit times are skipped in body/relative/subject parsers and LLM post-processing.
2. **Calendar sender ignore** — downvoting a calendar event ignores the sender for future extraction and bulk-hides existing events.
3. **Manual per-email calendar extraction** — inbox "Add to calendar" button with status badges.
4. **Pipeline diagnostics** — "Investigate pipelines" replaces TTS-only investigate on the pipelines page; phrases/recordings pagination added.

The calendar parsing changes are well-tested and behaviorally sound. The sender-ignore and inbox-enrichment paths have correctness and performance gaps that should be addressed before merge.

**Verdict:** Request changes — fix the upvote/unhide asymmetry and sender-lookup performance before shipping.

---

## Findings

### 1. Upvote does not undo bulk-hide from sender ignore

| Field | Value |
|-------|-------|
| **Severity** | major |
| **File:Line** | `smartinbox/core.py:966-970`, `smartinbox/calendar_events.py:136-159` |
| **Status** | open |

**Description:** Downvoting a calendar event calls `_apply_calendar_sender_ignore`, which runs `hide_calendar_events_for_sender` and sets `hidden = 1` on **all** events from that sender. Upvoting later calls `_clear_calendar_sender_ignore`, which only removes the row from `calendar_ignored_senders`; `record_event_vote` unhides only the **single** event that was upvoted. Other events from the same sender remain hidden.

**Suggestion:** On upvote, either (a) unhide all events from that sender, or (b) add `unhide_calendar_events_for_sender` symmetric to the hide path. Document the chosen behavior in the calendar downvote tooltip.

---

### 2. Sender lookups scan entire emails and calendar_events tables

| Field | Value |
|-------|-------|
| **Severity** | major |
| **File:Line** | `smartinbox/calendar_events.py:124-141`, `smartinbox/calendar_events.py:136-159` |
| **Status** | open |

**Description:** `email_ids_for_sender_key` runs `SELECT id, sender FROM emails` and filters in Python with `normalize_sender`. `hide_calendar_events_for_sender` does the same for `calendar_events`. On a large mailbox, a single calendar downvote triggers two full-table scans plus N `mark_email_extracted` calls.

**Suggestion:** Store normalized `sender_key` on `emails` and `calendar_events` at ingest time (with an index), then use `WHERE sender_key = ?`. Short-term: at least batch the hide/update with a subquery once sender_key exists, or cache normalized senders.

---

### 3. N+1 queries when listing inbox emails

| Field | Value |
|-------|-------|
| **Severity** | minor |
| **File:Line** | `smartinbox/core.py:2872-2894` |
| **Status** | open |

**Description:** `_enrich_inbox_email_row` calls `calendar_extraction_event_count` per email. `list_emails_for_display(limit=50)` issues up to 51 queries on every poll/WebSocket refresh.

**Suggestion:** Join or batch-fetch from `calendar_extraction_log` in one query, e.g. `LEFT JOIN calendar_extraction_log l ON l.email_id = e.id` in `list_emails` or a dedicated enrichment query with `WHERE email_id IN (...)`.

---

### 4. Nearby time extraction may attach unrelated times

| Field | Value |
|-------|-------|
| **Severity** | minor |
| **File:Line** | `smartinbox/calendar_body.py:204-221`, `smartinbox/calendar_body.py:409-411` |
| **Status** | open |

**Description:** `_extract_nearby_time` searches up to 160 characters after a date match and returns the **first** time pattern found. Event listings like `Aug 05, 2026 • Ottawa` followed later by `doors at 7pm` could incorrectly pair the date with that time.

**Suggestion:** Prefer times immediately adjacent to the date (e.g. within 40 chars, or on the same line). Add a regression test for date-only event promos.

---

### 5. LLM source_text filter misses 24-hour times

| Field | Value |
|-------|-------|
| **Severity** | minor |
| **File:Line** | `smartinbox/calendar_extract.py:59-69` |
| **Status** | open |

**Description:** `_SOURCE_TIME_RE` requires `am/pm` or ISO `T` prefix. LLM events with `source_text` like `"2026-06-21 19:05:00"` or `"at 14:00"` are dropped even when `start` is valid.

**Suggestion:** Extend the regex to accept `\d{1,2}:\d{2}(?::\d{2})?` without am/pm when adjacent to a date, or validate against the parsed `start` timestamp instead of source_text alone.

---

### 6. No UI/API to list or manage calendar-ignored senders

| Field | Value |
|-------|-------|
| **Severity** | minor |
| **File:Line** | `smartinbox/calendar_events.py:106-121` |
| **Status** | open |

**Description:** `list_calendar_ignored_senders` is implemented but never exposed via `server.py` or settings UI. Users can only clear ignore by upvoting any event from that sender (which suffers from finding #1).

**Suggestion:** Add a settings section or calendar page list of ignored senders with remove actions.

---

### 7. Demo mode tracks ignored senders but does not enforce them

| Field | Value |
|-------|-------|
| **Severity** | minor |
| **File:Line** | `smartinbox/core.py:300`, `smartinbox/core.py:916-922`, `smartinbox/core.py:1027` |
| **Status** | open |

**Description:** `_demo_calendar_ignored_senders` is updated on vote but never read during extraction. Demo calendar extraction is disabled entirely, so impact is low, but demo vote behavior is inconsistent with production.

**Suggestion:** Either check `_demo_calendar_ignored_senders` in demo vote/logging paths consistently, or remove the unused set.

---

### 8. Unused import in `_apply_calendar_sender_ignore`

| Field | Value |
|-------|-------|
| **Severity** | nit |
| **File:Line** | `smartinbox/core.py:868` |
| **Status** | open |

**Description:** `from smartinbox.important_senders import normalize_sender` is imported but never used in the function body.

**Suggestion:** Remove the unused import.

---

### 9. Shared investigate status element for pipelines and TTS

| Field | Value |
|-------|-------|
| **Severity** | nit |
| **File:Line** | `smartinbox/web/static/js/process.js:1603-1604`, `smartinbox/web/templates/pipelines.html:89-96` |
| **Status** | open |

**Description:** Pipeline investigate and Chatterbox restart both write to `process-tts-investigate-status` and `process-tts-investigate-results`. Running one after the other overwrites the other's output.

**Suggestion:** Split into separate status/result containers, or label the panel dynamically and preserve last results per type.

---

### 10. Missing newline at end of phrases.js

| Field | Value |
|-------|-------|
| **Severity** | nit |
| **File:Line** | `smartinbox/web/static/js/phrases.js:507` |
| **Status** | open |

**Description:** File ends without a trailing newline (shown in diff as `\ No newline at end of file`).

**Suggestion:** Add trailing newline per project convention.

---

## What looks good

- **Explicit-time policy** is applied consistently across body parsers, relative dates, subject lines, LLM prompt, and `parse_extracted_events` post-filter.
- **Test coverage** for calendar body parsing is solid (6 cases covering natural dates, date-only skip, relative tomorrow, explicit `Date:` lines, ISO without hints, subject `@ time`).
- **Manual extraction** correctly bypasses sender ignore via `manual=True` while automatic pipeline respects `is_calendar_sender_ignored`.
- **Inbox UX** — calendar button states (`Add to calendar` / `Re-scan`), busy guard, and event-count badge are clear; demo mode disables the button.
- **Pipeline investigate** — thorough checks (mail accounts, polling, backlogs, Ollama probe, LLM timings) with actionable recommendations.
- **Phrases pagination** — clean `pageSlice` helper; select-all scoped to current page is correct.

---

## Test gaps

| Area | Suggested tests |
|------|-----------------|
| Sender ignore on downvote | Hide all events, skip backlog, manual re-extract still works |
| Upvote after downvote | Verify unhide behavior matches intended design |
| `investigate_pipelines` | Mock pipeline status; assert check structure |
| `_SOURCE_TIME_RE` | 24-hour and ISO datetime in source_text |
| `_enrich_inbox_email_row` | Batch query correctness; `CALENDAR_QUEUE_SKIP_EVENT_COUNT` (−1) shows Re-scan |

---

## Checklist

| Check | Result |
|-------|--------|
| Tests pass | ✅ 6/6 |
| SQL injection | ✅ Parameterized queries; dynamic `IN` clause uses bound placeholders |
| Breaking API changes | ⚠️ Calendar downvote semantics changed (now ignores sender) — UI tooltip updated |
| Performance at scale | ❌ Full-table scans on ignore; N+1 on inbox list |
| Error handling | ✅ API endpoints return structured errors |