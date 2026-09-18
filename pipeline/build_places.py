"""Build the dataset the Flask app serves.

    python pipeline/build_places.py

Reads  data/italy.json          source of truth, never edited
Writes data/places.build.json   committed; this is what server/main.py loads

Everything slow or non-deterministic lives here rather than in the app: the LLM
calls, the pandas dependency, the hours parsing. LLM answers are cached in
    data/hours_llm_cache.json, so a rebuild that introduces no new hour strings
makes no API calls at all.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
from pathlib import Path

import pandas as pd
from defaults import get_default_duration, get_default_hours_fields
from llm_hours_parser import cache_key, load_hours_cache, parse_hours, save_hours_cache
from regex_hours_parser import parse_posted_hours

ROOT = Path(__file__).resolve().parent.parent

SOURCE = ROOT / "data" / "italy.json"
OUTPUT = ROOT / "data" / "places.build.json"

# A cold build is a few dozen short calls; this is well under any rate limit.
MAX_LLM_WORKERS = 8

def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """The notebook's cleanups, unchanged in spirit."""
    df = df.rename(columns={"hours": "posted_hours"})

    # 9 rows have no duration; fall back to a per-type guess.
    df["duration_minutes"] = df["duration_minutes"].fillna(df["type"].apply(get_default_duration))

    # 1 row has no booking_required.
    df["booking_required"] = df["booking_required"].fillna(False)

    # italy.json spells it both local_favorite and local-favorite.
    df["tags"] = df["tags"].explode().str.replace("_", "-").groupby(level=0).agg(list)

    return df


def to_rows(df: pd.DataFrame) -> list[dict]:
    """Records with NaN as None, and the numeric columns back to plain int/bool.

    pandas widens a column to float as soon as it holds one null, so without this
    you get duration_minutes 120.0 and booking_required 1.0 in the artifact.
    """
    rows = df.astype(object).where(df.notna(), None).to_dict("records")
    for row in rows:
        row["duration_minutes"] = int(row["duration_minutes"])
        row["booking_required"] = bool(row["booking_required"])
        row["rating"] = float(row["rating"])
        row["latitude"] = float(row["latitude"])
        row["longitude"] = float(row["longitude"])
    return rows


def route_hours(row: dict) -> tuple[str, dict | None]:
    """Say where this row's hours come from, and give them unless the LLM owes them.

    Split out from resolve_hours so prefetch_llm_hours can ask the question without
    making the call: both have to pick the same rows or the prefetch misses some.
    """
    posted = row["posted_hours"]
    parsed = parse_posted_hours(posted) if posted else None

    # The LLM only reads prose: seasonal notes, or a string the regex cannot handle.
    if row["seasonal_notes"] or (posted and parsed is None):
        return "llm", None
    if parsed:
        return "posted", parsed
    # Nothing posted and nothing to read it from; assume this type's usual window.
    return "inferred", get_default_hours_fields(row["type"])


def prefetch_llm_hours(rows: list[dict], cache: dict) -> None:
    """Fill the cache for every row the LLM has to read, with the calls in flight together.

    Distinct (hours, notes) pairs only, so rows sharing a string still cost one call --
    the row-by-row build got that dedup for free by filling the cache as it went.
    """
    pending = {}
    for row in rows:
        if route_hours(row)[0] != "llm":
            continue
        key = cache_key(row["posted_hours"], row["seasonal_notes"])
        if key not in cache:
            pending[key] = (row["posted_hours"], row["seasonal_notes"])

    if not pending:
        return

    print(f"LLM: {len(pending)} uncached hour strings, up to {MAX_LLM_WORKERS} at a time")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_LLM_WORKERS) as pool:
        calls = {
            pool.submit(parse_hours, posted, notes): key
            for key, (posted, notes) in pending.items()
        }
        for call in concurrent.futures.as_completed(calls):
            key = calls[call]
            # Raises on the first failure, same as the row-by-row build did.
            cache[key] = call.result()
            print(f"  parsed {pending[key][0]!r} notes={pending[key][1]!r}")


def llm_hours(posted, notes, cache: dict) -> dict:
    key = cache_key(posted, notes)
    if key not in cache:
        cache[key] = parse_hours(posted, notes)
    return cache[key]


def resolve_hours(row: dict, cache: dict) -> tuple[dict, str]:
    """Turn a posted hours string into open_days/open_months, and say where it came from."""
    source, hours = route_hours(row)
    if source == "llm":
        hours = llm_hours(row["posted_hours"], row["seasonal_notes"], cache)
    return hours, source


def build() -> list[dict]:
    rows = to_rows(clean_frame(pd.read_json(SOURCE)))
    cache = load_hours_cache()
    cached_before = len(cache)

    # Every LLM call happens here, so the loop below is pure cache reads.
    prefetch_llm_hours(rows, cache)

    places = []
    sources = collections.Counter()
    no_open_days = []

    for row in rows:
        hours, source = resolve_hours(row, cache)
        sources[source] += 1
        if not hours["open_days"]:
            no_open_days.append(row["id"])
        places.append({**row, **hours, "hours_source": source})

    if len(cache) > cached_before:
        save_hours_cache(cache)
        print(f"cache: {len(cache) - cached_before} new entries saved")

    print(f"{len(places)} places | hours from: {dict(sources)}")
    if no_open_days:
        print(f"WARNING: no open days at all: {no_open_days}")
    return places


if __name__ == "__main__":
    places = build()
    OUTPUT.write_text(
        json.dumps(places, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size:,} bytes)")
