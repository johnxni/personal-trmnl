"""Tests for scrape_webcal parsing and expansion."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def test_exdate_single(load_edge_case):
    text = load_edge_case('exdate_single.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_exdate_multi_line(load_edge_case):
    text = load_edge_case('exdate_multi_line.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_exdate_comma_list(load_edge_case):
    text = load_edge_case('exdate_comma_list.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_cancellation_override(load_edge_case):
    text = load_edge_case('cancellation_override.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


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


def test_all_day_skipped(load_edge_case):
    text = load_edge_case('all_day.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_status_cancelled_skipped(load_edge_case):
    text = load_edge_case('status_cancelled.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_transp_transparent_skipped(load_edge_case):
    text = load_edge_case('transp_transparent.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


def test_skip_keyword_filtered(load_edge_case):
    text = load_edge_case('skip_keyword.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == []


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


def test_multi_day_event(load_edge_case):
    text = load_edge_case('multi_day_event.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 22, 0),
            'end': _dt(2026, 5, 1, 8, 0),
            'summary': 'Overnight Trip',
        }
    ]


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


def test_floating_datetime_localized_to_display_timezone(load_edge_case):
    text = load_edge_case('floating_datetime.ics')
    events = parse_ics_text(text, TZ, TODAY, TOMORROW)
    today_events = [e for e in events if e['start'].date() == TODAY]
    assert today_events == [
        {
            'start': _dt(2026, 4, 30, 14, 0),
            'end': _dt(2026, 4, 30, 15, 0),
            'summary': 'Floating Time Event',
        }
    ]
