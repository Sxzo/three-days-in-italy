"""Parse simple posted_hours strings. Unknown shapes return None (use the LLM).

Shapes (see classify_shape):
  vague         Morning only / Evenings
  daily         Daily 10:00-24:00
  tt            9:00-19:00
  dd_tt         Tues-Sun 8:15-19:00
  dd_tt_tt      Tues-Sat 12:30-14:30, 19:30-22:00
  dd_tt_dd_tt   Mon-Sat 9:30-17:30, Sun 14:00-17:30
                (each side is a day or day-range plus one time range)
  tt_tt         12:00-14:30, 19:00-22:30
  dd_list_tt    Tues, Thurs-Sun 10:00-18:00
                (comma-separated days and/or day-ranges, one time range)
"""

from __future__ import annotations

import re

# 0=Sunday ... 6=Saturday. First 3 letters of a day name map here (Tues -> tue).
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
ALL_WEEKS = [1, 2, 3, 4, 5, -1]
ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

VAGUE_HOURS = {
    "morning only": [{"open": "08:00", "close": "12:00"}],
    "evenings": [{"open": "18:00", "close": "23:00"}],
}

TIME = r"(\d{1,2}(?::\d{2})?(?:am|pm)?)"  # 9:00, 8am, 12:30pm
DAY = r"([A-Za-z]+)"
# One clause: "Sun 14:00-17:30" or "Mon-Sat 9:30-17:30"
CLAUSE = rf"{DAY}(?:-{DAY})? {TIME}-{TIME}"
# "Tues, Thurs-Sun" — at least two comma-separated day tokens, each day or day-range.
# Non-capturing inside: the whole list is captured once and split in the parser.
_DAY = r"[A-Za-z]+"
DAY_LIST = rf"{_DAY}(?:-{_DAY})?(?:, {_DAY}(?:-{_DAY})?)+"

PATTERNS = {
    "daily": re.compile(rf"^Daily {TIME}-{TIME}$", re.I),
    "tt": re.compile(rf"^{TIME}-{TIME}$", re.I),
    "dd_tt": re.compile(rf"^{DAY}-{DAY} {TIME}-{TIME}$", re.I),
    "dd_tt_tt": re.compile(rf"^{DAY}-{DAY} {TIME}-{TIME}, {TIME}-{TIME}$", re.I),
    "dd_tt_dd_tt": re.compile(rf"^{CLAUSE}, {CLAUSE}$", re.I),
    "tt_tt": re.compile(rf"^{TIME}-{TIME}, {TIME}-{TIME}$", re.I),
    "dd_list_tt": re.compile(rf"^({DAY_LIST}) {TIME}-{TIME}$", re.I),
}


def classify_shape(text: str) -> str | None:
    """Return which template this string is, or None if we cannot parse it."""
    if text.lower() in VAGUE_HOURS:
        return "vague"
    # two-clause before dd_tt_tt so "Mon-Sat ..., Sun ..." is not a same-day split.
    # dd_tt_tt before dd_tt so "Tues-Sat 12:00-14:00, 19:00-22:00" keeps both ranges.
    for shape in ("daily", "dd_tt_dd_tt", "dd_tt_tt", "dd_list_tt", "dd_tt", "tt_tt", "tt"):
        if PATTERNS[shape].fullmatch(text):
            return shape
    return None


def to_hhmm(token: str) -> str:
    """'8am' -> '08:00', '12:30pm' -> '12:30', '19:00' -> '19:00'."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", token, flags=re.I)
    hour = int(m[1])
    minute = int(m[2] or 0)
    ampm = (m[3] or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def to_window(open_str: str, close_str: str) -> dict:
    """One open/close pair. Close-before-open (8:00-01:00) becomes close 25:00."""
    open_hhmm = to_hhmm(open_str)
    close_hhmm = to_hhmm(close_str)
    open_mins = int(open_hhmm[:2]) * 60 + int(open_hhmm[3:])
    close_mins = int(close_hhmm[:2]) * 60 + int(close_hhmm[3:])
    if close_mins <= open_mins:
        close_mins += 24 * 60
        close_hhmm = f"{close_mins // 60:02d}:{close_mins % 60:02d}"
    return {"open": open_hhmm, "close": close_hhmm}


def day_indexes(start_day: str, end_day: str) -> list[int]:
    """Inclusive range that wraps: Wed-Mon -> [3, 4, 5, 6, 0, 1]."""
    start = DAYS.index(start_day[:3].lower())
    end = DAYS.index(end_day[:3].lower())
    count = (end - start) % 7 + 1
    return [(start + i) % 7 for i in range(count)]


def result(days: list[int], windows: list[dict]) -> dict:
    return {
        "open_days": {
            day: {"hours": [dict(w) for w in windows], "weeks": list(ALL_WEEKS)}
            for day in days
        },
        "open_months": list(ALL_MONTHS),
    }


def parse_vague(text: str) -> dict:
    return result(list(range(7)), VAGUE_HOURS[text.lower()])


def parse_daily(text: str) -> dict:
    open1, close1 = PATTERNS["daily"].fullmatch(text).groups()
    return result(list(range(7)), [to_window(open1, close1)])


def parse_tt(text: str) -> dict:
    open1, close1 = PATTERNS["tt"].fullmatch(text).groups()
    return result(list(range(7)), [to_window(open1, close1)])


def parse_dd_tt(text: str) -> dict:
    start, end, open1, close1 = PATTERNS["dd_tt"].fullmatch(text).groups()
    return result(day_indexes(start, end), [to_window(open1, close1)])


def parse_dd_tt_tt(text: str) -> dict:
    start, end, open1, close1, open2, close2 = PATTERNS["dd_tt_tt"].fullmatch(text).groups()
    windows = [to_window(open1, close1), to_window(open2, close2)]
    return result(day_indexes(start, end), windows)


def parse_dd_tt_dd_tt(text: str) -> dict:
    """Two clauses; the second overwrites any overlapping days."""
    start1, end1, open1, close1, start2, end2, open2, close2 = (
        PATTERNS["dd_tt_dd_tt"].fullmatch(text).groups()
    )
    parsed = result(day_indexes(start1, end1 or start1), [to_window(open1, close1)])
    parsed["open_days"].update(
        result(day_indexes(start2, end2 or start2), [to_window(open2, close2)])["open_days"]
    )
    return parsed


def parse_tt_tt(text: str) -> dict:
    """Two windows in one day, no day prefix: every day gets both."""
    open1, close1, open2, close2 = PATTERNS["tt_tt"].fullmatch(text).groups()
    return result(list(range(7)), [to_window(open1, close1), to_window(open2, close2)])


def parse_dd_list_tt(text: str) -> dict:
    """One time range applied to every day named in the list."""
    day_list, open1, close1 = PATTERNS["dd_list_tt"].fullmatch(text).groups()
    days: list[int] = []
    for token in day_list.split(","):
        bounds = token.strip().split("-")
        for day in day_indexes(bounds[0], bounds[-1]):
            if day not in days:
                days.append(day)
    return result(days, [to_window(open1, close1)])


PARSERS = {
    "vague": parse_vague,
    "daily": parse_daily,
    "tt": parse_tt,
    "dd_tt": parse_dd_tt,
    "dd_tt_tt": parse_dd_tt_tt,
    "dd_tt_dd_tt": parse_dd_tt_dd_tt,
    "tt_tt": parse_tt_tt,
    "dd_list_tt": parse_dd_list_tt,
}


def parse_posted_hours(raw: str | None) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    shape = classify_shape(text)
    if shape is None:
        return None
    return PARSERS[shape](text)
