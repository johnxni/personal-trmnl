import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from config import TRMNL_WEBHOOK_URL, TRMNL_CALENDAR_URLS

DEFAULT_TIMEZONE = "America/Los_Angeles"

SKIP_KEYWORDS = ["#skip"]

# Configure logging to show INFO messages
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def load_config():
    config = {"WEBHOOK_URL": os.environ["TRMNL_WEBHOOK_URL"],
              "CALENDAR_URLS": [url for url in os.environ["TRMNL_CALENDAR_URLS"].split(",") if url]}

    return config


def get_ics(url):
    """Fetch the ICS data from the given URL."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]

    response = requests.get(url)
    response.raise_for_status()
    return response.text


def parse_events(ics_text):
    events = []
    current = {}
    in_event = False

    for line in ics_text.splitlines():
        line = line.strip()

        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
            continue

        if line == "END:VEVENT":
            in_event = False
            events.append(current)
            continue

        if not in_event:
            continue

        if ":" in line:
            key, val = line.split(":", 1)
            current[key] = val

    return events


def parse_dt(val):
    # example: 20260103T131500
    return datetime.strptime(val, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


def parse_webcal(calendar_url):
    """Read from an apple webcal and parse into JSON format.

    example event
    {'DTEND;TZID=America/Los_Angeles': '20260103T131500',
     'DTSTART;TZID=America/Los_Angeles': '20260103T123000',
     'UID': 'ASDF-AB9C-123E-1234-12FHIDAFH',
     'DTSTAMP': '20260103T200111Z',
     'X-APPLE-CREATOR-IDENTITY': 'com.apple.mobilecal',
     'URL;VALUE=URI': '',
     'SEQUENCE': '0',
     'SUMMARY': 'Example Sumary',
     'LAST-MODIFIED': '20251229T022642Z',
     'CREATED': '20251229T022642Z',
     'X-APPLE-CREATOR-TEAM-IDENTITY': '0000000000'}
    """

    text = get_ics(calendar_url)
    data = parse_events(text)

    events = []
    for item in data:
        event = {}
        keys = item.keys()
        start_key = [k for k in keys if k.startswith("DTSTART")][0]
        end_key = [k for k in keys if k.startswith("DTEND")][0]
        if "VALUE=DATE" in start_key or "VALUE=DATE" in end_key:
            logging.info("Skipping all-day event: %s", item["SUMMARY"])
            continue

        event["start"] = parse_dt(item[start_key])
        event["end"] = parse_dt(item[end_key])
        event["summary"] = item.get("SUMMARY", "No Summary")

        events.append(event)

    return events


def build_payload(keep_events, today_date, tomorrow_date):
    payload = {
        "today": {
            "date": today_date.strftime("%A, %B %d"),
            "year": today_date.year,
            "timezone": DEFAULT_TIMEZONE,
            "events": [],
        },
        "tomorrow": {
            "date": tomorrow_date.strftime("%A, %B %d"),
            "year": tomorrow_date.year,
            "timezone": DEFAULT_TIMEZONE,
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
        json.dump(payload, f, indent=2)
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


def main(skip_keywords=None, dry_run=True, force_update=False):
    today_date = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    tomorrow_date = today_date + timedelta(days=1)

    config = load_config()
    keep_events = []
    for calendar_url in config["CALENDAR_URLS"]:
        events = parse_webcal(calendar_url)
        for event in events:
            if event["start"].date() in [today_date, tomorrow_date]:
                keep_events.append(event)

    keep_events.sort(key=lambda e: e["start"])

    payload = build_payload(keep_events, today_date, tomorrow_date)

    previous_payload = load_payload()
    if force_update or payload_checksum(payload) != payload_checksum(previous_payload):
        logging.info("Payload has changed.")
        if dry_run:
            logging.info("Dry run: not uploading. Payload: %s", json.dumps(payload, indent=2))
        else:
            upload_calendar_json(payload, config["WEBHOOK_URL"])
            save_payload(payload)
    else:
        logging.info("No changes detected; skipping upload.")

    logging.info("Finished execution.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-update", action="store_true")
    args = parser.parse_args()

    main(dry_run=args.dry_run, force_update=args.force_update)
