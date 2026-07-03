from __future__ import annotations

import re
from datetime import datetime, timedelta


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern, fmt in (
        (r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", "%Y/%m/%d"),
        (r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", None),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if fmt is not None:
                return datetime.strptime("/".join(match.groups()), fmt)
            return datetime(int(match.group(3)), _MONTHS[match.group(2).casefold()], int(match.group(1)))
        except (ValueError, KeyError):
            return None
    try:
        return datetime.fromisoformat(text[:10])
    except ValueError:
        return None


def format_date(value: datetime) -> str:
    return value.strftime("%Y/%m/%d")


def resolve_relative_time(anchor: str, timestamp: str) -> str:
    raw = " ".join(str(anchor or "").split()).strip().casefold()
    parsed = parse_timestamp(timestamp)
    if not raw or parsed is None:
        return ""
    offsets = {
        "yesterday": -1,
        "the previous day": -1,
        "previous day": -1,
        "tomorrow": 1,
        "the next day": 1,
        "next day": 1,
        "last week": -7,
        "the previous week": -7,
        "previous week": -7,
        "last weekend": -7,
        "the weekend before": -7,
        "weekend before": -7,
    }
    if raw in offsets:
        return format_date(parsed + timedelta(days=offsets[raw]))
    if raw in {"last month", "previous month", "the previous month"}:
        month = parsed.month - 1
        year = parsed.year
        if month < 1:
            month = 12
            year -= 1
        return f"{year:04d}/{month:02d}"
    if raw in {"next month", "following month", "the next month"}:
        month = parsed.month + 1
        year = parsed.year
        if month > 12:
            month = 1
            year += 1
        return f"{year:04d}/{month:02d}"
    if raw == "for a month":
        return "one month"
    return ""

