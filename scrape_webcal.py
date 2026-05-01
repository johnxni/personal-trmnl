import argparse
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

from config import TRMNL_CALENDAR_URLS, TRMNL_WEBHOOK_URLS

DEFAULT_TIMEZONE = "America/Los_Angeles"

SKIP_KEYWORDS = ["#skip"]

# Configure logging to show INFO messages
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def load_config():
    config = {"TRMNL_WEBHOOK_URLS": TRMNL_WEBHOOK_URLS,
              "TRMNL_CALENDAR_URLS": TRMNL_CALENDAR_URLS}

    return config


def get_ics(url):
    """Fetch the ICS data from the given URL."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]

    response = requests.get(url)
    response.raise_for_status()
    return response.text


def parse_ics_text(text: str, display_timezone: str, today_date, tomorrow_date) -> list[dict]:
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
        # All-day events come back as date, not datetime - skip.
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        events.append({
            'start': start.astimezone(tz),
            'end': end.astimezone(tz),
            'summary': str(event.get('SUMMARY', 'No Summary')),
        })

    events = [e for e in events if not any(k in e['summary'] for k in SKIP_KEYWORDS)]
    events.sort(key=lambda e: e['start'])
    return events


def _keep_event(event) -> bool:
    """
    Return whether an expanded VEVENT should be kept.

    Parameters
    ----------
    event : icalendar.Event
        Expanded VEVENT from ``recurring_ical_events``.

    Returns
    -------
    bool
        ``True`` if the event is busy and not cancelled.
    """
    if str(event.get('TRANSP', '')).upper() == 'TRANSPARENT':
        return False
    # recurring-ical-events still returns occurrences with STATUS:CANCELLED
    # (including overrides that cancel a recurring instance); suppress them here.
    if str(event.get('STATUS', '')).upper() == 'CANCELLED':
        return False
    return True


def parse_webcal(calendar_url: str, display_timezone: str, today_date, tomorrow_date) -> list[dict]:
    """
    Fetch and parse an iCalendar URL into expanded events.

    Parameters
    ----------
    calendar_url : str
        URL to the iCalendar feed (``webcal://`` or ``https://``).
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
    text = get_ics(calendar_url)
    return parse_ics_text(text, display_timezone, today_date, tomorrow_date)


def build_payload(keep_events, today_date, tomorrow_date, display_timezone):
    payload = {
        "today": {
            "date": today_date.strftime("%A, %B %d"),
            "year": today_date.year,
            "timezone": display_timezone,
            "events": [],
        },
        "tomorrow": {
            "date": tomorrow_date.strftime("%A, %B %d"),
            "year": tomorrow_date.year,
            "timezone": display_timezone,
            "events": [],
        },
    }

    for event in keep_events:
        if event["start"].date() == today_date:
            day_key = "today"
        else:
            day_key = "tomorrow"

        start = event["start"].strftime("%I:%M %p").lstrip("0")
        end = event["end"].strftime("%I:%M %p").lstrip("0")
        data = {"start": start, "end": end, "summary": event["summary"]}
        payload[day_key]["events"].append(data)

    return payload


def upload_calendar_json(payload, webhook_url):
    headers = {"Content-Type": "application/json"}
    merge_variable_payload = {"merge_variables": payload}
    response = requests.post(webhook_url, json=merge_variable_payload, headers=headers)

    if response.status_code == 200:
        logging.info("Successfully uploaded calendar data to %s", webhook_url)
    else:
        logging.error(
            "Failed to upload calendar data to %s. Status code: %d, Response: %s",
            webhook_url,
            response.status_code,
            response.text,
        )


def payload_checksum(payload):
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.md5(data).hexdigest()


def save_payload(payload):
    filename = "calendar_payload.json"
    with open(filename, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logging.info("Saved payload to %s", filename)


def load_payload(filename="calendar_payload.json"):
    try:
        with open(filename, "r") as f:
            payload = json.load(f)
        logging.info("Loaded payload from %s", filename)
        return payload
    except FileNotFoundError:
        logging.warning("Payload file %s not found. Returning empty payload.", filename)
        return {}


def main(display_timezone=DEFAULT_TIMEZONE, dry_run=True, force_update=False, datestr=None):
    if datestr is None:
        today_date = datetime.now(ZoneInfo(display_timezone)).date()
    else:
        today_date = datetime.strptime(datestr, "%Y%m%d").date()
    tomorrow_date = today_date + timedelta(days=1)

    config = load_config()
    all_events = []
    for calendar_url in config["TRMNL_CALENDAR_URLS"]:
        events = parse_webcal(calendar_url, display_timezone, today_date, tomorrow_date)
        all_events.extend(events)
    all_events.sort(key=lambda e: e["start"])

    keep_events = [e for e in all_events if e["start"].date() in [today_date, tomorrow_date]]
    keep_events.sort(key=lambda e: e["start"])

    payload = build_payload(keep_events, today_date, tomorrow_date, display_timezone)

    previous_payload = load_payload()
    if force_update or payload_checksum(payload) != payload_checksum(previous_payload):
        logging.info("Payload has changed.")
        if dry_run:
            logging.info("Dry run: not uploading. Payload: %s", json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for webhook_url in config["TRMNL_WEBHOOK_URLS"]:
                upload_calendar_json(payload, webhook_url)
            save_payload(payload)
    else:
        logging.info("No changes detected; skipping upload.")

    logging.info("Finished execution.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-update", action="store_true")
    parser.add_argument("--display-timezone", type=str, default=DEFAULT_TIMEZONE)
    parser.add_argument("--datestr", type=str, default=None, help="YYYYMMDD; overrides 'today'")
    args = parser.parse_args()

    main(display_timezone=args.display_timezone, dry_run=args.dry_run, force_update=args.force_update, datestr=args.datestr)
