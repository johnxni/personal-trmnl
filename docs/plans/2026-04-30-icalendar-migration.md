# icalendar Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hand-rolled iCalendar parser and recurrence expander in `scrape_webcal.py` with `icalendar` + `recurring-ical-events`, backed by a regression test suite covering known edge cases.

**Architecture:** Two-library swap. `icalendar` parses `.ics` text into structured `Calendar` objects. `recurring-ical-events` expands recurrences over a date range, correctly handling `RRULE`, `EXDATE`, `RDATE`, `RECURRENCE-ID` overrides, and `STATUS:CANCELLED`. We keep `get_ics`, `build_payload`, webhook code, etc. unchanged, and replace `parse_events` + `parse_webcal` + `parse_rrule` + `expand_recurring_events` with a thin wrapper.

**Tech Stack:** Python 3.14, `uv`, `pytest` (new dev dep), `icalendar` (new), `recurring-ical-events` (new). Existing: `requests`, `python-dateutil`.

**Reference design:** [docs/plans/2026-04-30-icalendar-migration-design.md](2026-04-30-icalendar-migration-design.md)

**Frozen test date:** `2026-04-30` (Thursday, PDT/UTC-7) for all edge-case tests.

---

## Phase 1 — Baseline & Test Scaffolding

### Task 1: Reset working tree to clean baseline

**Why:** Working tree has uncommitted debug instrumentation and partial fixes from the prior debugging session. The migration reintroduces correct behavior; starting clean removes confusion.

**Files:**
- Reset: `scrape_webcal.py`

**Step 1: Verify what will be reverted**

```bash
git diff scrape_webcal.py
```

Expected: shows the `DEBUG_SUMMARY_MATCH` instrumentation, EXDATE parsing additions, and the empty-segment filter in `parse_rrule`.

**Step 2: Confirm with user** before discarding changes (this is destructive).

**Step 3: Discard uncommitted changes**

```bash
git checkout -- scrape_webcal.py
```

**Step 4: Verify clean state**

```bash
git status scrape_webcal.py
```

Expected: nothing to commit on this file.

**Step 5: No commit needed.**

---

### Task 2: Add pytest as dev dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add pytest dev dep group**

```bash
uv add --dev pytest
```

**Step 2: Verify pyproject.toml has dev group**

Read `pyproject.toml`. Expected new section:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
]
```

(`uv` may pick a different lower bound; that's fine.)

**Step 3: Verify install**

```bash
uv run pytest --version
```

Expected: prints `pytest 8.x.y`.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "add pytest dev dependency"
```

---

### Task 3: Create tests directory structure

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/fixtures/edge_cases/.gitkeep` (empty)
- Modify: `.gitignore`

**Step 1: Make directories**

```bash
mkdir -p tests/fixtures/edge_cases tests/fixtures/local
touch tests/__init__.py tests/fixtures/edge_cases/.gitkeep
```

**Step 2: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures for the test suite."""
from pathlib import Path

import pytest

EDGE_CASES_DIR = Path(__file__).parent / 'fixtures' / 'edge_cases'


@pytest.fixture
def load_edge_case():
    """
    Return a loader that reads an edge-case fixture file by name.

    Returns
    -------
    callable
        Function ``load(name)`` returning the file contents as ``str``.
    """
    def _load(name: str) -> str:
        return (EDGE_CASES_DIR / name).read_text()
    return _load
```

**Step 3: Add `tests/fixtures/local/` to `.gitignore`**

Append to `.gitignore`:

```
tests/fixtures/local/
```

**Step 4: Add pytest config to `pyproject.toml`**

Append:

```toml
[tool.pytest.ini_options]
testpaths = ['tests']
```

**Step 5: Verify pytest discovers an empty suite**

```bash
uv run pytest -q
```

Expected: `no tests ran` (or `0 tests collected`).

**Step 6: Commit**

```bash
git add tests/ .gitignore pyproject.toml
git commit -m "scaffold tests directory and pytest config"
```

---

### Task 4: Refactor `parse_webcal` to expose a text-only entry point

**Why:** Tests must drive the parser without HTTP. Extract a `parse_ics_text(text, display_timezone, today_date, tomorrow_date)` function that does the parsing work; make `parse_webcal` a thin wrapper that calls `get_ics` then `parse_ics_text`. This is a no-op refactor — same behavior — that makes everything testable.

**Note:** The current `parse_webcal` does not honor `today_date`/`tomorrow_date` (it returns a 365-day window and the filtering happens in `main`). The new signature reflects the **future** boundary: callers pass the day range they care about. For Phase 1 (tests against current code), `parse_ics_text` ignores the date params and returns everything `parse_webcal` already returns; the date params become live in Phase 4.

**Files:**
- Modify: `scrape_webcal.py:80-155`

**Step 1: Extract function**

Refactor `parse_webcal` so the body that operates on parsed text moves into a new function:

```python
def parse_ics_text(text, display_timezone, today_date, tomorrow_date):
    """
    Parse iCalendar text into expanded events for the given day window.

    Parameters
    ----------
    text : str
        Raw iCalendar text.
    display_timezone : str
        IANA timezone name for output datetimes.
    today_date : datetime.date
        Inclusive start of the day window.
    tomorrow_date : datetime.date
        Inclusive end of the day window.

    Returns
    -------
    list[dict]
        List of events with keys ``start``, ``end``, ``summary``.
    """
    # During Phase 1 this delegates to the existing parse path and ignores
    # the date params; Phase 4 replaces the body with icalendar +
    # recurring-ical-events and uses today_date / tomorrow_date.
    data = parse_events(text)
    raw_events = _build_event_dicts(data, display_timezone)
    return expand_recurring_events(raw_events)


def parse_webcal(calendar_url, display_timezone, today_date, tomorrow_date):
    """Fetch and parse an iCalendar URL into expanded events."""
    text = get_ics(calendar_url)
    return parse_ics_text(text, display_timezone, today_date, tomorrow_date)
```

Move the per-VEVENT building loop out of the current `parse_webcal` into a private helper `_build_event_dicts(data, display_timezone)` that returns the raw event dicts (with `RRULE`, `RECURRENCE-ID`, `EXDATE`, etc., still attached for the expander). Keep all current behavior, including the `STATUS:CANCELLED` skip and DEBUG instrumentation — they were reset by Task 1 anyway, so the body is the original committed version.

**Step 2: Update `main()` to pass today/tomorrow into `parse_webcal`**

Replace:

```python
events = parse_webcal(calendar_url, display_timezone)
expanded_events = expand_recurring_events(events)
all_events.extend(expanded_events)
```

With:

```python
events = parse_webcal(calendar_url, display_timezone, today_date, tomorrow_date)
all_events.extend(events)
```

`expand_recurring_events` is now called inside `parse_ics_text`.

**Step 3: Run script to verify no behavior change**

```bash
uv run scrape_webcal.py --dry-run --datestr 20260430
```

Expected: same output as before refactor (same payload).

**Step 4: Commit**

```bash
git add scrape_webcal.py
git commit -m "extract parse_ics_text for testability"
```

---

## Phase 2 — Edge Case Test Suite

For **each** of the following tasks, the procedure is:

1. Create the `.ics` fixture under `tests/fixtures/edge_cases/`.
2. Add a parametrized test case (or new test function) in `tests/test_parse.py`.
3. Run `uv run pytest -q tests/test_parse.py::<test_name> -v`.
4. If the test passes against current code: commit.
5. If the test fails (current code has a known bug): mark with `@pytest.mark.xfail(reason="...", strict=False)` and commit. The xfail flips to pass after Phase 4 migration.

Set up the test file once in Task 5, then add cases.

**Common fixture conventions:**
- `DTSTART`/`DTEND` use `TZID=America/Los_Angeles` unless the test specifically targets DST or another zone.
- All recurring events use a Thursday-anchored `FREQ=WEEKLY` for consistency with the real-world `Razmik` event.
- Test date is always `today=2026-04-30`, `tomorrow=2026-05-01`.
- Each `.ics` is a complete `VCALENDAR` with `VERSION:2.0` and `PRODID`.

---

### Task 5: Create test file with first edge case (rrule_until — should pass today)

**Files:**
- Create: `tests/fixtures/edge_cases/rrule_until.ics`
- Create: `tests/test_parse.py`

**Step 1: Write fixture `tests/fixtures/edge_cases/rrule_until.ics`**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:rrule-until-001
SUMMARY:Weekly Standup
DTSTART;TZID=America/Los_Angeles:20260312T140000
DTEND;TZID=America/Los_Angeles:20260312T143000
RRULE:FREQ=WEEKLY;UNTIL=20260702T210000Z
END:VEVENT
END:VCALENDAR
```

This event recurs every Thursday from 2026-03-12 through 2026-07-02. Today (2026-04-30, Thursday) should produce one occurrence at 2:00 PM.

**Step 2: Write `tests/test_parse.py`**

```python
"""Tests for scrape_webcal parsing and expansion."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scrape_webcal import parse_ics_text


TZ = 'America/Los_Angeles'
TODAY = date(2026, 4, 30)
TOMORROW = date(2026, 5, 1)


def _dt(y, mo, d, h, mi, tz=TZ):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz))


def test_rrule_until(load_edge_case):
    text = load_edge_case('rrule_until.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 14, 0),
            'end': _dt(2026, 4, 30, 14, 30),
            'summary': 'Weekly Standup',
        }
    ]
```

**Step 3: Run test**

```bash
uv run pytest tests/test_parse.py::test_rrule_until -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/rrule_until.ics tests/test_parse.py
git commit -m "add rrule_until edge case test"
```

---

### Task 6: rrule_count edge case

**Files:**
- Create: `tests/fixtures/edge_cases/rrule_count.ics`
- Modify: `tests/test_parse.py` (add test function)

**Step 1: Write fixture**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:rrule-count-001
SUMMARY:Limited Recurrence
DTSTART;TZID=America/Los_Angeles:20260423T100000
DTEND;TZID=America/Los_Angeles:20260423T103000
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
END:VCALENDAR
```

Recurs 4 times: 4/23, 4/30, 5/7, 5/14. Today (4/30) is occurrence #2.

**Step 2: Add test function**

```python
def test_rrule_count(load_edge_case):
    text = load_edge_case('rrule_count.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 10, 0),
            'end': _dt(2026, 4, 30, 10, 30),
            'summary': 'Limited Recurrence',
        }
    ]
```

**Step 3: Run**

```bash
uv run pytest tests/test_parse.py::test_rrule_count -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/rrule_count.ics tests/test_parse.py
git commit -m "add rrule_count edge case test"
```

---

### Task 7: rrule_until_and_count edge case

**Files:**
- Create: `tests/fixtures/edge_cases/rrule_until_and_count.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:rrule-both-001
SUMMARY:Has Both
DTSTART;TZID=America/Los_Angeles:20260423T110000
DTEND;TZID=America/Los_Angeles:20260423T113000
RRULE:FREQ=WEEKLY;UNTIL=20260514T180000Z;COUNT=20
END:VEVENT
END:VCALENDAR
```

RFC says one or the other; `dateutil` and `icalendar` should reject or pick one. Current code logs a warning and ignores `COUNT`. Both libraries should produce the 4/30 occurrence regardless.

**Step 2: Test**

```python
def test_rrule_until_and_count(load_edge_case):
    text = load_edge_case('rrule_until_and_count.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 11, 0),
            'end': _dt(2026, 4, 30, 11, 30),
            'summary': 'Has Both',
        }
    ]
```

**Step 3: Run**

```bash
uv run pytest tests/test_parse.py::test_rrule_until_and_count -v
```

Expected: PASS (current code logs a warning and proceeds).

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/rrule_until_and_count.ics tests/test_parse.py
git commit -m "add rrule_until_and_count edge case test"
```

---

### Task 8: exdate_single — KNOWN FAILURE (xfail)

**Why this is xfail:** Current code does not read `EXDATE`. The test describes correct behavior; it will fail until Phase 4. Mark `xfail(strict=False)` so the suite stays green; Phase 4 removes the marker.

**Files:**
- Create: `tests/fixtures/edge_cases/exdate_single.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — recurring weekly with one excluded date matching today.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:exdate-single-001
SUMMARY:Weekly Lesson
DTSTART;TZID=America/Los_Angeles:20260312T153000
DTEND;TZID=America/Los_Angeles:20260312T161500
RRULE:FREQ=WEEKLY;UNTIL=20260702T223000Z
EXDATE;TZID=America/Los_Angeles:20260430T153000
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
@pytest.mark.xfail(reason='current parser ignores EXDATE; fixed by icalendar migration', strict=False)
def test_exdate_single(load_edge_case):
    text = load_edge_case('exdate_single.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run**

```bash
uv run pytest tests/test_parse.py::test_exdate_single -v
```

Expected: XFAIL.

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/exdate_single.ics tests/test_parse.py
git commit -m "add exdate_single edge case test (xfail)"
```

---

### Task 9: exdate_multi_line — KNOWN FAILURE (xfail)

**Why this is xfail:** Current parser overwrites duplicate `EXDATE` keys in the dict, then doesn't read them anyway.

**Files:**
- Create: `tests/fixtures/edge_cases/exdate_multi_line.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — two `EXDATE` lines, one matches today.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:exdate-multi-001
SUMMARY:Recurring with two EXDATEs
DTSTART;TZID=America/Los_Angeles:20260312T140000
DTEND;TZID=America/Los_Angeles:20260312T143000
RRULE:FREQ=WEEKLY;UNTIL=20260702T210000Z
EXDATE;TZID=America/Los_Angeles:20260423T140000
EXDATE;TZID=America/Los_Angeles:20260430T140000
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
@pytest.mark.xfail(reason='current parser collapses multi-line EXDATE; fixed by icalendar migration', strict=False)
def test_exdate_multi_line(load_edge_case):
    text = load_edge_case('exdate_multi_line.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run**

```bash
uv run pytest tests/test_parse.py::test_exdate_multi_line -v
```

Expected: XFAIL.

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/exdate_multi_line.ics tests/test_parse.py
git commit -m "add exdate_multi_line edge case test (xfail)"
```

---

### Task 10: exdate_comma_list — KNOWN FAILURE (xfail)

**Files:**
- Create: `tests/fixtures/edge_cases/exdate_comma_list.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — single `EXDATE` line with comma-separated dates.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:exdate-comma-001
SUMMARY:Recurring with comma EXDATE
DTSTART;TZID=America/Los_Angeles:20260312T143000
DTEND;TZID=America/Los_Angeles:20260312T150000
RRULE:FREQ=WEEKLY;UNTIL=20260702T210000Z
EXDATE;TZID=America/Los_Angeles:20260423T143000,20260430T143000,20260507T143000
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
@pytest.mark.xfail(reason='current parser ignores EXDATE; fixed by icalendar migration', strict=False)
def test_exdate_comma_list(load_edge_case):
    text = load_edge_case('exdate_comma_list.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run, expect XFAIL.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/exdate_comma_list.ics tests/test_parse.py
git commit -m "add exdate_comma_list edge case test (xfail)"
```

---

### Task 11: cancellation_override — KNOWN FAILURE (xfail)

**Why xfail:** Current code skips the override `VEVENT` (because `STATUS:CANCELLED`) before reading its `RECURRENCE-ID`, so the deletion never registers in the override map.

**Files:**
- Create: `tests/fixtures/edge_cases/cancellation_override.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — master + cancellation override for 2026-04-30.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:cancellation-001
SUMMARY:Recurring with cancelled occurrence
DTSTART;TZID=America/Los_Angeles:20260312T160000
DTEND;TZID=America/Los_Angeles:20260312T163000
RRULE:FREQ=WEEKLY;UNTIL=20260702T230000Z
END:VEVENT
BEGIN:VEVENT
UID:cancellation-001
RECURRENCE-ID;TZID=America/Los_Angeles:20260430T160000
DTSTART;TZID=America/Los_Angeles:20260430T160000
DTEND;TZID=America/Los_Angeles:20260430T163000
SUMMARY:Recurring with cancelled occurrence
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
@pytest.mark.xfail(reason='current code skips override before recording RECURRENCE-ID; fixed by icalendar migration', strict=False)
def test_cancellation_override(load_edge_case):
    text = load_edge_case('cancellation_override.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run, expect XFAIL.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/cancellation_override.ics tests/test_parse.py
git commit -m "add cancellation_override edge case test (xfail)"
```

---

### Task 12: modified_override (renamed/rescheduled occurrence)

**Files:**
- Create: `tests/fixtures/edge_cases/modified_override.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — override that changes the SUMMARY for the 4/30 occurrence (no STATUS:CANCELLED).

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:modified-001
SUMMARY:Original Summary
DTSTART;TZID=America/Los_Angeles:20260312T170000
DTEND;TZID=America/Los_Angeles:20260312T173000
RRULE:FREQ=WEEKLY;UNTIL=20260702T230000Z
END:VEVENT
BEGIN:VEVENT
UID:modified-001
RECURRENCE-ID;TZID=America/Los_Angeles:20260430T170000
DTSTART;TZID=America/Los_Angeles:20260430T173000
DTEND;TZID=America/Los_Angeles:20260430T180000
SUMMARY:Override Summary
END:VEVENT
END:VCALENDAR
```

The override moves the 4/30 instance from 5:00–5:30 to 5:30–6:00 with a new title.

**Step 2: Test**

```python
def test_modified_override(load_edge_case):
    text = load_edge_case('modified_override.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 17, 30),
            'end': _dt(2026, 4, 30, 18, 0),
            'summary': 'Override Summary',
        }
    ]
```

**Step 3: Run**

```bash
uv run pytest tests/test_parse.py::test_modified_override -v
```

If this PASSES on current code: commit without xfail.
If it FAILS (because current code emits both the master occurrence AND the override): add `@pytest.mark.xfail(reason='current code may emit both; fixed by migration', strict=False)`.

**Note for the implementer:** The current `parse_webcal` records `RECURRENCE-ID` as an override datetime, but the override `VEVENT` is also emitted as a standalone event because it has no `RRULE`. So today's events would contain both `Override Summary` *and* the original `Original Summary` at 17:00 (from RRULE expansion suppressed by the override). Verify with the test run.

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/modified_override.ics tests/test_parse.py
git commit -m "add modified_override edge case test"
```

---

### Task 13: all_day event (should be skipped)

**Files:**
- Create: `tests/fixtures/edge_cases/all_day.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — non-recurring all-day event on 4/30.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:allday-001
SUMMARY:Birthday
DTSTART;VALUE=DATE:20260430
DTEND;VALUE=DATE:20260501
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
def test_all_day_skipped(load_edge_case):
    text = load_edge_case('all_day.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run, expect PASS.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/all_day.ics tests/test_parse.py
git commit -m "add all_day edge case test"
```

---

### Task 14: status_cancelled non-recurring (should be skipped)

**Files:**
- Create: `tests/fixtures/edge_cases/status_cancelled.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:cancelled-001
SUMMARY:Cancelled Meeting
DTSTART;TZID=America/Los_Angeles:20260430T100000
DTEND;TZID=America/Los_Angeles:20260430T103000
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
def test_status_cancelled_skipped(load_edge_case):
    text = load_edge_case('status_cancelled.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run, expect PASS.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/status_cancelled.ics tests/test_parse.py
git commit -m "add status_cancelled edge case test"
```

---

### Task 15: transp_transparent (should be skipped)

**Files:**
- Create: `tests/fixtures/edge_cases/transp_transparent.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:transp-001
SUMMARY:Free Time Block
DTSTART;TZID=America/Los_Angeles:20260430T120000
DTEND;TZID=America/Los_Angeles:20260430T130000
TRANSP:TRANSPARENT
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
def test_transp_transparent_skipped(load_edge_case):
    text = load_edge_case('transp_transparent.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 3: Run, expect PASS.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/transp_transparent.ics tests/test_parse.py
git commit -m "add transp_transparent edge case test"
```

---

### Task 16: skip_keyword

**Note:** The `#skip` filter currently lives in `main()`, not in `parse_webcal`. So `parse_ics_text` returns the event with `#skip` in the summary. Either:
- Move the skip filter into `parse_ics_text` (preferred — keeps `keep_event` predicate together post-migration).
- Or test against `main()` output via the dry-run path.

Choose **move into `parse_ics_text`**. Update Task 4's refactor to apply skip-keyword filtering inside `parse_ics_text` (or its private helpers). If Task 4 is already done without this, do a small follow-up modification here.

**Files:**
- Modify: `scrape_webcal.py` (move `#skip` filter into `parse_ics_text`; remove from `main`)
- Create: `tests/fixtures/edge_cases/skip_keyword.ics`
- Modify: `tests/test_parse.py`

**Step 1: Move `#skip` filter**

In `parse_ics_text`, after expansion, filter:

```python
events = [e for e in events if not any(k in e['summary'] for k in SKIP_KEYWORDS)]
```

Remove the equivalent loop in `main()`.

**Step 2: Fixture**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:skip-001
SUMMARY:Lunch #skip
DTSTART;TZID=America/Los_Angeles:20260430T123000
DTEND;TZID=America/Los_Angeles:20260430T130000
END:VEVENT
END:VCALENDAR
```

**Step 3: Test**

```python
def test_skip_keyword_filtered(load_edge_case):
    text = load_edge_case('skip_keyword.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []
```

**Step 4: Run, expect PASS.**

**Step 5: Verify `main()` still works**

```bash
uv run scrape_webcal.py --dry-run --datestr 20260430
```

Expected: same output as before this task.

**Step 6: Commit**

```bash
git add scrape_webcal.py tests/fixtures/edge_cases/skip_keyword.ics tests/test_parse.py
git commit -m "move #skip filter into parse_ics_text and add test"
```

---

### Task 17: folded_long_line — KNOWN FAILURE (xfail)

**Why xfail:** Current parser doesn't unfold long lines per RFC 5545.

**Files:**
- Create: `tests/fixtures/edge_cases/folded_long_line.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — line folding splits a long SUMMARY across two lines (continuation begins with single space).

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:folded-001
SUMMARY:This is a very long event summary that the producer has chosen
  to fold across two physical lines per RFC 5545 section 3.1
DTSTART;TZID=America/Los_Angeles:20260430T140000
DTEND;TZID=America/Los_Angeles:20260430T150000
END:VEVENT
END:VCALENDAR
```

The two physical lines should be unfolded to a single logical SUMMARY.

**Step 2: Test**

```python
@pytest.mark.xfail(reason='current parser does not unfold lines; fixed by icalendar migration', strict=False)
def test_folded_long_line(load_edge_case):
    text = load_edge_case('folded_long_line.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert len(today_events) == 1
    expected_summary = (
        'This is a very long event summary that the producer has chosen '
        'to fold across two physical lines per RFC 5545 section 3.1'
    )
    assert today_events[0]['summary'] == expected_summary
```

**Step 3: Run, expect XFAIL.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/folded_long_line.ics tests/test_parse.py
git commit -m "add folded_long_line edge case test (xfail)"
```

---

### Task 18: multi_day_event (today/tomorrow bucketing)

**Files:**
- Create: `tests/fixtures/edge_cases/multi_day_event.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — non-recurring event spanning 4/30 evening into 5/1 morning.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:multiday-001
SUMMARY:Overnight Trip
DTSTART;TZID=America/Los_Angeles:20260430T220000
DTEND;TZID=America/Los_Angeles:20260501T080000
END:VEVENT
END:VCALENDAR
```

**Step 2: Test**

```python
def test_multi_day_event(load_edge_case):
    text = load_edge_case('multi_day_event.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    # Event starts on 4/30; under current bucketing it's a 'today' event.
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 22, 0),
            'end': _dt(2026, 5, 1, 8, 0),
            'summary': 'Overnight Trip',
        }
    ]
```

**Step 3: Run, expect PASS.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/multi_day_event.ics tests/test_parse.py
git commit -m "add multi_day_event edge case test"
```

---

### Task 19: dst_transition

**Files:**
- Create: `tests/fixtures/edge_cases/dst_transition.ics`
- Modify: `tests/test_parse.py`

**Step 1: Fixture** — recurring weekly Tuesday 2PM Los_Angeles, spans the 2026-03-08 DST start.

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:dst-001
SUMMARY:Weekly DST Test
DTSTART;TZID=America/Los_Angeles:20260303T140000
DTEND;TZID=America/Los_Angeles:20260303T150000
RRULE:FREQ=WEEKLY;COUNT=10
END:VEVENT
END:VCALENDAR
```

Occurrences: 3/3 (PST), 3/10 (PDT), 3/17 (PDT), … Both 3/3 and 3/10 should display as 2:00 PM in `America/Los_Angeles`.

**Step 2: Test** — assert against the 3/3 and 3/10 occurrences (use a different TODAY for this test, since 4/30 is past the early DST window).

```python
def test_dst_transition(load_edge_case):
    text = load_edge_case('dst_transition.ics')
    today = date(2026, 3, 3)
    tomorrow = date(2026, 3, 4)
    events = parse_ics_text(text, TZ, today, tomorrow)
    today_events = [e for e in events if e['start'].date() == today]
    assert today_events == [
        {
            'start': _dt(2026, 3, 3, 14, 0),
            'end': _dt(2026, 3, 3, 15, 0),
            'summary': 'Weekly DST Test',
        }
    ]

    # Now query the post-DST week
    today2 = date(2026, 3, 10)
    tomorrow2 = date(2026, 3, 11)
    events2 = parse_ics_text(text, TZ, today2, tomorrow2)
    today_events2 = [e for e in events2 if e['start'].date() == today2]
    assert today_events2 == [
        {
            'start': _dt(2026, 3, 10, 14, 0),
            'end': _dt(2026, 3, 10, 15, 0),
            'summary': 'Weekly DST Test',
        }
    ]
```

`_dt(...)` produces `datetime` with `ZoneInfo('America/Los_Angeles')`, which is DST-aware, so equality holds.

**Step 3: Run, expect PASS.**

**Step 4: Commit**

```bash
git add tests/fixtures/edge_cases/dst_transition.ics tests/test_parse.py
git commit -m "add dst_transition edge case test"
```

---

### Task 20: Verify the full test suite state

**Files:** none.

**Step 1: Run entire suite**

```bash
uv run pytest -v
```

**Step 2: Count expected outcomes**

Expected:
- PASS: rrule_until, rrule_count, rrule_until_and_count, all_day_skipped, status_cancelled_skipped, transp_transparent_skipped, skip_keyword_filtered, dst_transition
- XFAIL: exdate_single, exdate_multi_line, exdate_comma_list, cancellation_override, modified_override, multi_day_event, folded_long_line

Total: 15 tests. ~8 pass, ~7 xfail. (`test_modified_override` and `test_multi_day_event` both xfail because the current parser leaks UID/RECURRENCE-ID dict keys for non-recurring VEVENTs; the migration produces clean `{start, end, summary}` dicts. Recurring events in the other PASS tests pass because `expand_recurring_events` builds clean dicts when expanding RRULE occurrences.)

**Step 3: No commit.**

---

## Phase 3 — Local Snapshot Test

**Status: SKIPPED.** The current parser crashes on the live calendar feed (a pre-existing RRULE empty-segment bug Phase 4 fixes), so a "current behavior" baseline cannot be captured. Per user decision on 2026-04-30, this phase is skipped entirely. The migration proceeds without a snapshot regression guard. The original task descriptions are kept below for reference but are not executed.

### Task 21 [SKIPPED]: Capture local snapshot fixture (gitignored)

**Files:**
- Create: `tests/fixtures/local/snapshot.ics`
- Create: `tests/fixtures/local/snapshot_expected.json`

**Step 1: Fetch the live calendar text**

Use the helper script approach. From a Python REPL or one-liner:

```bash
uv run python -c "
from scrape_webcal import get_ics, load_config
cfg = load_config()
for i, url in enumerate(cfg['TRMNL_CALENDAR_URLS']):
    text = get_ics(url)
    with open(f'tests/fixtures/local/snapshot_{i}.ics', 'w') as f:
        f.write(text)
print(f'wrote {len(cfg[\"TRMNL_CALENDAR_URLS\"])} fixture(s)')
"
```

If only one calendar URL, the file is `snapshot_0.ics`. Adjust the snapshot test below to match.

**Step 2: Generate expected payload**

```bash
uv run python -c "
import json
from datetime import date
from scrape_webcal import parse_ics_text

today = date(2026, 4, 30)
tomorrow = date(2026, 5, 1)
text = open('tests/fixtures/local/snapshot_0.ics').read()
events = parse_ics_text(text, 'America/Los_Angeles', today, tomorrow)
serializable = [
    {'start': e['start'].isoformat(), 'end': e['end'].isoformat(), 'summary': e['summary']}
    for e in events
]
with open('tests/fixtures/local/snapshot_expected.json', 'w') as f:
    json.dump(serializable, f, indent=2, ensure_ascii=False)
print(f'wrote {len(serializable)} events')
"
```

**Step 3: Verify both files exist and are gitignored**

```bash
ls tests/fixtures/local/
git status tests/fixtures/local/
```

Expected: files present; `git status` does NOT list them (they match `.gitignore`).

**Step 4: No commit (files are gitignored).**

---

### Task 22 [SKIPPED]: Add snapshot parity test

**Files:**
- Modify: `tests/test_parse.py`

**Step 1: Add test that loads from local fixtures and skips if missing**

```python
import json
from pathlib import Path

LOCAL_DIR = Path(__file__).parent / 'fixtures' / 'local'


def test_local_snapshot_parity():
    snapshot = LOCAL_DIR / 'snapshot_0.ics'
    expected_path = LOCAL_DIR / 'snapshot_expected.json'
    if not snapshot.exists() or not expected_path.exists():
        pytest.skip('local snapshot fixtures not present')

    text = snapshot.read_text()
    expected = json.loads(expected_path.read_text())

    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    actual = [
        {'start': e['start'].isoformat(), 'end': e['end'].isoformat(), 'summary': e['summary']}
        for e in events
    ]
    assert actual == expected
```

**Step 2: Run**

```bash
uv run pytest tests/test_parse.py::test_local_snapshot_parity -v
```

Expected: PASS (you just generated the expected from the same parser; this is the "as of now" baseline).

**Step 3: Commit**

```bash
git add tests/test_parse.py
git commit -m "add local snapshot parity test"
```

---

## Phase 4 — Migrate to icalendar + recurring-ical-events

### Task 23: Add libraries

**Files:**
- Modify: `pyproject.toml` (via `uv add`)

**Step 1: Add deps**

```bash
uv add icalendar recurring-ical-events
```

**Step 2: Verify versions**

```bash
uv run python -c "import icalendar, recurring_ical_events; print(icalendar.__version__, recurring_ical_events.__version__)"
```

Expected: prints two version strings (recent — 6.x for icalendar, 3.x for recurring-ical-events as of late 2025).

**Step 3: Verify lockfile updated**

```bash
git diff uv.lock | head -50
```

Expected: shows new entries for both packages.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "add icalendar and recurring-ical-events"
```

---

### Task 24: Replace parser internals with library calls

**Files:**
- Modify: `scrape_webcal.py`

**Step 1: Rewrite `parse_ics_text`**

Replace the body and delete `parse_events`, `parse_rrule`, `expand_recurring_events`, `_build_event_dicts`, and `parse_dt` (no longer needed):

```python
import recurring_ical_events
from icalendar import Calendar


def parse_ics_text(text, display_timezone, today_date, tomorrow_date):
    """
    Parse iCalendar text into expanded events for the given day window.

    Parameters
    ----------
    text : str
        Raw iCalendar text.
    display_timezone : str
        IANA timezone name for output datetimes.
    today_date : datetime.date
        Inclusive start of the day window.
    tomorrow_date : datetime.date
        Inclusive end of the day window.

    Returns
    -------
    list[dict]
        List of events with keys ``start``, ``end``, ``summary``.
    """
    tz = ZoneInfo(display_timezone)
    range_start = datetime.combine(today_date, datetime.min.time(), tzinfo=tz)
    range_end = datetime.combine(tomorrow_date, datetime.max.time(), tzinfo=tz)

    calendar = Calendar.from_ical(text)
    expanded = recurring_ical_events.of(calendar).between(range_start, range_end)

    events = []
    for event in expanded:
        if not _keep_event(event):
            continue
        start = event['DTSTART'].dt
        end = event['DTEND'].dt
        # All-day events come back as date, not datetime — skip.
        if not isinstance(start, datetime):
            continue
        events.append({
            'start': start.astimezone(tz),
            'end': end.astimezone(tz),
            'summary': str(event.get('SUMMARY', 'No Summary')),
        })

    events = [e for e in events if not any(k in e['summary'] for k in SKIP_KEYWORDS)]
    events.sort(key=lambda e: e['start'])
    return events


def _keep_event(event):
    if str(event.get('TRANSP', '')).upper() == 'TRANSPARENT':
        return False
    # STATUS:CANCELLED is suppressed by recurring-ical-events automatically;
    # this guard handles direct usage edge cases.
    if str(event.get('STATUS', '')).upper() == 'CANCELLED':
        return False
    return True
```

Delete:
- `parse_events` function.
- `parse_dt` function.
- `parse_rrule` function.
- `expand_recurring_events` function.
- `_build_event_dicts` (if added in Task 4).
- `DEBUG_SUMMARY_MATCH` constant (if still present from prior debug session).
- `from dateutil import rrule` import.

Keep `parse_webcal` as the URL → text → events wrapper.

**Step 2: Update `main()`**

`main()` already calls `parse_webcal(...)` with `(url, display_timezone, today_date, tomorrow_date)` from Task 4. No change required, but remove the `#skip` filter loop in `main()` if it's still there (Task 16 should have moved it; verify).

**Step 3: Run the live script to sanity check**

```bash
uv run scrape_webcal.py --dry-run --datestr 20260430
```

Expected: payload prints; no crash; the `Razmik` 4/30 occurrence we were debugging should NOT appear (because `EXDATE` is now honored).

**Step 4: Run pytest**

```bash
uv run pytest -v
```

Expected:
- All previously-passing tests still pass.
- All previously-xfail tests now XPASS (or, if `strict=False`, just pass quietly with `XPASS` notation).
- Snapshot parity test: likely fails (because the fixed code produces a different payload than the snapshot baseline captured against the buggy code). That's the expected fallout — handled in Task 25.

**Step 5: Do not commit yet** — wait for Task 25 to clean up xfail markers and snapshot.

---

### Task 25: Remove xfail markers

**Files:**
- Modify: `tests/test_parse.py` (remove xfail decorators on the 7 known-failure tests)

**Note:** Phase 3 (snapshot fixture) was skipped, so the local snapshot refresh step is also dropped. Verification relies on the 15 edge-case tests in `tests/test_parse.py`.

**Step 1: Remove xfail markers**

Delete `@pytest.mark.xfail(...)` from:
- `test_exdate_single`
- `test_exdate_multi_line`
- `test_exdate_comma_list`
- `test_cancellation_override`
- `test_modified_override`
- `test_multi_day_event`
- `test_folded_long_line`

**Step 2: Run those tests**

```bash
uv run pytest tests/test_parse.py::test_exdate_single tests/test_parse.py::test_exdate_multi_line tests/test_parse.py::test_exdate_comma_list tests/test_parse.py::test_cancellation_override tests/test_parse.py::test_modified_override tests/test_parse.py::test_multi_day_event tests/test_parse.py::test_folded_long_line -v
```

Expected: all PASS.

**Step 3: Run full suite**

```bash
uv run pytest -v
```

Expected: all 16 tests PASS, no xfail remaining. (15 originally-planned edge cases plus `test_floating_datetime_localized_to_display_timezone` added during Task 24 to lock in floating-datetime localization.)

**Step 4: Commit migration**

```bash
git add scrape_webcal.py tests/fixtures/edge_cases/floating_datetime.ics tests/test_parse.py
git commit -m "$(cat <<'EOF'
migrate parsing and recurrence to icalendar + recurring-ical-events

Replaces hand-rolled parse_events / parse_rrule / expand_recurring_events
with icalendar.Calendar.from_ical and recurring_ical_events.of(...).between(...).

Fixes (now covered by tests):
  - EXDATE single, multi-line, and comma-list forms
  - Cancellation overrides (RECURRENCE-ID + STATUS:CANCELLED)
  - RFC 5545 line folding
  - RRULE empty-segment crash

See docs/plans/2026-04-30-icalendar-migration-design.md for design rationale.
EOF
)"
```

---

## Phase 5 — Verify Pinning

### Task 26: Confirm lockfile pinning is correct

**Files:**
- Inspect: `pyproject.toml`, `uv.lock`

**Step 1: Verify `pyproject.toml` constraints are lower-bound only**

Read `pyproject.toml`. Expected:

```toml
dependencies = [
    "icalendar>=...",
    "python-dateutil>=2.9.0.post0",
    "recurring-ical-events>=...",
    "requests>=2.32.5",
]
```

(`python-dateutil` may now be unused — verify with `uv run python -c "from dateutil import rrule"` failing on a fresh check, or grep the source. Remove if unused: `uv remove python-dateutil`.)

**Step 2: Verify `uv.lock` has concrete versions**

```bash
grep -E '^name = "(icalendar|recurring-ical-events)"' uv.lock
```

Expected: both names present with `version = "..."` lines below.

**Step 3: Verify `uv.lock` is committed**

```bash
git ls-files uv.lock
```

Expected: prints `uv.lock`.

**Step 4: If `python-dateutil` was removed, commit**

```bash
git add pyproject.toml uv.lock
git commit -m "drop unused python-dateutil"
```

Otherwise: no commit.

---

### Task 27: Final verification

**Files:** none.

**Step 1: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests PASS, no xfail, no errors.

**Step 2: Run live script end-to-end**

```bash
uv run scrape_webcal.py --dry-run --datestr 20260430
```

Expected: payload prints; the deleted `Razmik` 4/30 occurrence is absent; output otherwise matches expectations.

**Step 3: Verify nothing uncommitted**

```bash
git status
```

Expected: clean working tree (modulo gitignored `tests/fixtures/local/`).

**Step 4: Review the migration commit chain**

```bash
git log --oneline | head -30
```

Expected: clear sequence of small commits — pytest setup → individual edge case tests → migration → snapshot refresh.

---

## Notes for the Implementer

- **Frequent commits:** every task ends with a commit. If a task has multiple steps, only the final step commits. If you find yourself wanting to commit mid-task, the task is too big — split it.
- **xfail strategy:** `strict=False` so that a passing xfail-marked test is reported `XPASS` but not a failure. Phase 4 removes the markers, at which point the tests must outright pass.
- **Snapshot fixture:** stays gitignored throughout. Only the test file references it; the test skips when the fixture is absent (e.g., on CI without the personal calendar).
- **Date format in tests:** all assertions use `datetime` literals via `_dt(...)`. The library may return tzinfo objects with subtly different identity (e.g., `pytz` vs `zoneinfo`); after migration, ensure equality holds via `astimezone(ZoneInfo(display_timezone))` in the parser and `ZoneInfo(...)` in the test helper. Both use stdlib `zoneinfo`, so they should compare equal.
- **`recurring-ical-events` behavior:** by default, the library *includes* events whose start is within the range. Single-day events that started before the range but extend into it are also returned. This matches our needs (multi-day events landing on `today`).
- **`STATUS:CANCELLED` master events:** `recurring-ical-events` skips these by default — verify with `test_status_cancelled_skipped`. The `_keep_event` predicate's `STATUS:CANCELLED` guard is belt-and-suspenders.
- **If something doesn't fit this plan:** stop and ask. The plan should not need creative reinterpretation.
