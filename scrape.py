#!/usr/bin/env python3
"""
Scrapes HKUST CSE seminar page and generates an ICS calendar file.
Usage: python3 scrape.py
Output: seminars.ics
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event, vText

SOURCE_URL = "https://cse.hkust.edu.hk/pg/seminars/"
BASE_URL = "https://cse.hkust.edu.hk"
HKT = ZoneInfo("Asia/Hong_Kong")

SKIP_TITLE_PATTERNS = [
    r"^no seminar",
    r"^\[rescheduled",
    r"^tba$",
    r"^\s*$",
]


def should_skip(title: str) -> bool:
    t = title.strip().lower()
    return any(re.match(p, t) for p in SKIP_TITLE_PATTERNS)


def parse_datetime(date_str: str, time_str: str):
    """Parse date like '02 Feb 2026' and time like '09:30' into a HKT-aware datetime."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%d %b %Y %H:%M")
    return dt.replace(tzinfo=HKT)


def extract_time_range(raw: str):
    """Extract start/end times from string like 'Mon (09:30-10:30)'."""
    m = re.search(r"\((\d{2}:\d{2})-(\d{2}:\d{2})\)", raw)
    if m:
        return m.group(1), m.group(2)
    return None, None


def extract_date(raw: str):
    """Extract date string like '02 Feb 2026' from the full date+time cell."""
    m = re.match(r"(\d{2} \w+ \d{4})", raw.strip())
    return m.group(1) if m else None


def scrape_seminars():
    resp = requests.get(SOURCE_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    seminars = []
    tables = soup.find_all("table", class_="fancy")

    for table in tables:
        # Determine section label from preceding h2
        section = ""
        prev = table.find_parent("div")
        if prev:
            h2 = prev.find_previous_sibling("h2")
            if h2:
                section = h2.get_text(strip=True)

        # Only process the COMP 6911 & COMP 6912 departmental seminars
        if "COMP 6911" not in section:
            continue

        for tr in table.find("tbody").find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 5:
                continue

            raw_date = cols[0].get_text(strip=True)
            date_str = extract_date(raw_date)
            start_time, end_time = extract_time_range(raw_date)

            if not date_str or not start_time:
                continue

            # Venue
            venue_td = cols[1]
            venue_link = venue_td.find("a")
            if venue_link:
                venue = venue_link.get_text(strip=True)
                venue_url = venue_link.get("href") or venue_link.get("xhref", "")
                if venue_url and venue_url.startswith("/"):
                    venue_url = BASE_URL + venue_url
            else:
                venue = venue_td.get_text(strip=True)
                venue_url = ""

            # Title + detail link
            title_td = cols[2]
            title_link = title_td.find("a")
            if title_link:
                title = title_link.get_text(" ", strip=True)
                detail_href = title_link.get("href") or title_link.get("xhref", "")
                detail_url = (BASE_URL + detail_href) if detail_href and detail_href.startswith("/") else detail_href or ""
            else:
                title = title_td.get_text(" ", strip=True)
                detail_url = ""

            if should_skip(title):
                continue

            # Speaker (name + affiliation separated by <br>)
            speaker_td = cols[3]
            speaker_parts = [t.strip() for t in speaker_td.stripped_strings]
            speaker_name = speaker_parts[0] if speaker_parts else ""
            speaker_affil = speaker_parts[1] if len(speaker_parts) > 1 else ""

            host = cols[4].get_text(strip=True)

            try:
                dtstart = parse_datetime(date_str, start_time)
                dtend = parse_datetime(date_str, end_time)
            except ValueError:
                continue

            seminars.append({
                "title": title,
                "dtstart": dtstart,
                "dtend": dtend,
                "venue": venue,
                "venue_url": venue_url,
                "speaker_name": speaker_name,
                "speaker_affil": speaker_affil,
                "host": host,
                "detail_url": detail_url,
                "section": section,
            })

    return seminars


def build_description(s: dict) -> str:
    parts = []
    if s["speaker_name"]:
        parts.append(f"Speaker: {s['speaker_name']}")
    if s["speaker_affil"]:
        parts.append(f"Affiliation: {s['speaker_affil']}")
    if s["host"]:
        parts.append(f"Host: {s['host']}")
    if s["detail_url"] and not s["detail_url"].endswith("/.html"):
        parts.append(f"Details: {s['detail_url']}")
    parts.append(f"Source: {SOURCE_URL}")
    return "\n".join(parts)


def build_ics(seminars: list) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//HKUST CSE Seminars//scraper//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "HKUST CSE Seminars")
    cal.add("x-wr-timezone", "Asia/Hong_Kong")
    cal.add("x-wr-caldesc", f"Automatically generated from {SOURCE_URL}")
    cal.add("refresh-interval;value=duration", "PT6H")
    cal.add("x-published-ttl", "PT6H")

    for s in seminars:
        ev = Event()
        ev.add("summary", s["title"])
        ev.add("dtstart", s["dtstart"])
        ev.add("dtend", s["dtend"])

        location_parts = []
        if s["venue"]:
            location_parts.append(s["venue"])
            if "zoom" not in s["venue"].lower():
                location_parts.append("HKUST, Hong Kong")
        if location_parts:
            ev.add("location", ", ".join(location_parts))

        ev.add("description", build_description(s))

        if s["detail_url"] and not s["detail_url"].endswith("/.html"):
            ev.add("url", s["detail_url"])

        # Stable UID based on start time + title slug
        uid_slug = re.sub(r"[^a-z0-9]", "", s["title"].lower())[:30]
        uid = f"{s['dtstart'].strftime('%Y%m%dT%H%M%S')}-{uid_slug}@cse.hkust.edu.hk"
        ev.add("uid", uid)

        ev.add("dtstamp", datetime.now(tz=timezone.utc))

        cal.add_component(ev)

    return cal


def main():
    print("Fetching seminars from", SOURCE_URL)
    seminars = scrape_seminars()
    print(f"Found {len(seminars)} seminars")

    cal = build_ics(seminars)
    output = "seminars.ics"
    with open(output, "wb") as f:
        f.write(cal.to_ical())
    print(f"Written to {output}")


if __name__ == "__main__":
    main()
