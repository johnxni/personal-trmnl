# icalendar Migration Design

**Date:** 2026-04-30
**Status:** Approved, ready for implementation planning

## Motivation

`scrape_webcal.py` hand-parses `.ics` text and expands recurring events. We have repeatedly hit edge cases the hand-rolled code does not handle:

- `EXDATE` (RFC 5545 occurrence exclusion) was never read, so deleted occurrences of recurring events still appeared.
- Cancellation overrides (`STATUS:CANCELLED` + `RECURRENCE-ID`) were skipped before their `RECURRENCE-ID` could be recorded as a suppression.
- `RRULE` strings containing empty `;` segments crashed `dateutil.rrule.rrulestr`.
- Multiple `EXDATE` lines collapsed under a single dict key.
- RFC 5545 line folding is not handled.

Each fix is small in isolation, but the bug class keeps producing new instances. The `icalendar` library implements the full spec, and `recurring-ical-events` implements correct expansion (RRULE + EXDATE + RDATE + RECURRENCE-ID overrides + STATUS:CANCELLED). Replacing the hand-rolled parser eliminates this class of bugs and reduces code in `scrape_webcal.py`.

## Goals

1. Cover known edge cases with hand-crafted committed tests that describe **correct** behavior. Some will fail against `main` until migration completes; that is the success signal.
2. Produce a personal-feed snapshot test (gitignored) to guard against unexpected regressions during migration.
3. Replace `parse_events`, `parse_webcal`, `parse_rrule`, and `expand_recurring_events` with `icalendar` + `recurring-ical-events`.
4. Pin via `uv.lock`; constraints in `pyproject.toml` are lower-bound only.

## Non-Goals

- Refactoring `build_payload`, `upload_calendar_json`, `save_payload`, `load_payload`, or webhook logic.
- Adding per-calendar error isolation (one bad calendar still fails the whole run).
- Changing the JSON payload schema.

## Architecture

The new `parse_webcal(url, display_timezone, today_date, tomorrow_date)` returns `[{start, end, summary}, ...]` directly. Internals:

1. `get_ics(url)` — unchanged, fetches `.ics` text.
2. `icalendar.Calendar.from_ical(text)` — parses to a `Calendar` object.
3. `recurring_ical_events.of(calendar).between(start_dt, end_dt)` — returns expanded `Event` instances spanning the day window, with all recurrence rules and overrides applied.
4. For each expanded event, convert `start`/`end` to `display_timezone` via `astimezone(ZoneInfo(display_timezone))` and assemble the dict.
5. `keep_event(event)` predicate filters all-day events, `TRANSP:TRANSPARENT`, and `#skip` keyword. (`STATUS:CANCELLED` is already suppressed by `recurring-ical-events`.)

`expand_recurring_events`, `parse_events`, `parse_rrule`, and the `DEBUG_SUMMARY_MATCH` instrumentation are deleted. `main()` shrinks: no separate expansion step, the date range is passed in.

## Test Layout

```
tests/
├── conftest.py
├── fixtures/
│   ├── edge_cases/         # committed
│   │   ├── exdate_single.ics
│   │   ├── exdate_multi_line.ics
│   │   ├── exdate_comma_list.ics
│   │   ├── cancellation_override.ics
│   │   ├── modified_override.ics
│   │   ├── rrule_until.ics
│   │   ├── rrule_count.ics
│   │   ├── rrule_until_and_count.ics
│   │   ├── all_day.ics
│   │   ├── status_cancelled.ics
│   │   ├── transp_transparent.ics
│   │   ├── skip_keyword.ics
│   │   ├── folded_long_line.ics
│   │   ├── multi_day_event.ics
│   │   └── dst_transition.ics
│   └── local/              # gitignored
└── test_parse.py
```

Each test loads a fixture, calls `parse_webcal` with a frozen `today_date`, and asserts equality against a literal expected list defined inline in the test. `pytest` is added as a dev dependency. `tests/fixtures/local/` is added to `.gitignore`.

## Migration Order

1. Add `pytest` dev dep, scaffold `tests/`, write all edge-case fixtures + tests against current `parse_webcal`. Most pass; some fail (the known bugs). Commit. The failing tests are the migration spec.
2. Capture local snapshot: fetch live calendar to `tests/fixtures/local/snapshot.ics`, run current parser with frozen `today_date`, save expected payload to `tests/fixtures/local/snapshot_expected.json`. Add a parity test. Mark known-buggy diffs as `xfail`.
3. `uv add icalendar recurring-ical-events`. Lockfile updates.
4. Migrate `parse_webcal` to library-based implementation. Delete dead code and DEBUG instrumentation. All edge-case tests should pass; flip `xfail`s on snapshot test for deliberate fixes.
5. Verify `uv.lock` records concrete versions; constraints in `pyproject.toml` remain lower-bound only.
6. Commit the migration as a single cohesive change linking back to this design.

## Behavior Decisions

- **Timezone:** wall-clock-in-event-TZID semantics preserved. `2:00 PM` stays `2:00 PM` across DST when `display_timezone` matches the event's TZID. Cross-zone DST mismatches are preserved as-is (correct per spec, even when surprising).
- **Date range:** library is queried for `[today_00:00, tomorrow_23:59:59]` in `display_timezone`. The current 365-day expand-then-filter pattern goes away.
- **All-day events:** library returns these with `date` (not `datetime`) starts; `keep_event` skips them.
- **`STATUS:CANCELLED` master events:** suppressed by `recurring-ical-events`; verified by test, no extra code.
- **`TRANSP:TRANSPARENT` and `#skip`:** filtered in `keep_event` post-expansion.
- **Network/parse failures:** propagate, as today.

## Pinning

- `pyproject.toml` constraints are lower-bound (`>=`).
- `uv.lock` (already in repo) is the durable pin.
- Version bumps require an explicit `uv lock --upgrade-package <name>`.

## Out of Scope

- Per-calendar error isolation.
- Logging/observability changes beyond removing the temporary debug lines.
- Payload schema or webhook changes.
