"""LLM hours parsing for leftover hour strings and any row with seasonal notes.

open_days contract
------------------
A day missing from open_days is CLOSED.
A day present with an empty `hours` list is OPEN, time of day unknown - not closed.

The empty list is how a row with no posted hours records its days, weeks and months
without inventing a window. Read it as "no time constraint": never render it as a
closed day or as 00:00-00:00. Only rows whose `hours` column is null produce it;
a row with posted hours always carries its times.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Anchored to this file, not the cwd, so importing from anywhere works.
_HERE = Path(__file__).resolve().parent
PROMPT = (_HERE / "open_hours_prompt.txt").read_text(encoding="utf-8")
CACHE_PATH = _HERE.parent / "data" / "hours_llm_cache.json"

load_dotenv(_HERE.parent / ".env")

_client = None
_client_lock = threading.Lock()


def get_client():
    """Imported lazily: reading the cache must work without openai installed.

    Locked because callers parse hours from several threads at once; the client
    itself is safe to share, it just must not be built twice.
    """
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI

            _client = OpenAI()
    return _client


class TimeWindow(BaseModel):
    open: str = Field(description="24-hour start time, HH:MM")
    close: str = Field(description="24-hour end time, HH:MM")


class DaySchedule(BaseModel):
    # These Field descriptions ship to the model as JSON schema. Editing one changes
    # parse behaviour, so re-run the eval before touching it.
    day: int = Field(ge=0, le=6, description="0=Sunday ... 6=Saturday")
    # Empty list = open, time of day unknown. See the open_days contract above.
    hours: list[TimeWindow] = Field(description="Open intervals this weekday")
    weeks: list[int] = Field(
        description="Weeks of the month this weekday is open. 1=first week, -1=last week. Use [1,2,3,4,5,-1] if every week."
    )


class PlaceHours(BaseModel):
    open_days: list[DaySchedule] = Field(description="One entry per open weekday")
    open_months: list[int] = Field(description="Months 1-12 the place is open")


def cache_key(hours, seasonal_notes) -> str:
    notes = None if not seasonal_notes else seasonal_notes
    return json.dumps({"hours": hours, "seasonal_notes": notes}, ensure_ascii=False)


def normalize_hours_fields(fields: dict) -> dict:
    return {
        "open_days": {int(day): schedule for day, schedule in fields["open_days"].items()},
        "open_months": [int(month) for month in fields["open_months"]],
    }


def load_hours_cache() -> dict:
    """Read the on-disk cache. Keys are cache_key(hours, seasonal_notes)."""
    if not CACHE_PATH.exists():
        return {}
    text = CACHE_PATH.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    raw = json.loads(text)
    return {key: normalize_hours_fields(fields) for key, fields in raw.items()}


def save_hours_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_hours(hours, seasonal_notes) -> dict:
    notes = seasonal_notes or ""
    hours_text = hours if hours else "(none)"
    parsed = (
        get_client()
        .chat.completions.parse(
            # Cheaper alternatives: "gpt-5-mini", "gpt-5-nano", "gpt-4o-mini".
            model="gpt-5.6-sol",
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(hours=hours_text, seasonal_notes=notes),
                }
            ],
            response_format=PlaceHours,
        )
        .choices[0]
        .message.parsed
    )
    if parsed is None:
        raise ValueError(f"Failed hours parse hours={hours!r} notes={notes!r}")
    return normalize_hours_fields(
        {
            "open_days": {
                day.day: {
                    "hours": [window.model_dump() for window in day.hours],
                    "weeks": day.weeks,
                }
                for day in parsed.open_days
            },
            "open_months": parsed.open_months,
        }
    )
