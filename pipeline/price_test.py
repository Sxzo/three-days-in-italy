"""What a cold build of data/places.build.json costs in tokens and dollars.

    python pipeline/price_test.py           estimate from the cached answers
    python pipeline/price_test.py --live    spend one real call to measure instead

Scratch tool: nothing in the build imports it. It reuses route_hours and cache_key,
so "calls" is what a build with an empty cache would really send.

Offline, tokens are estimated at ~4 chars each and reasoning tokens can only be
assumed -- they are billed as output but never appear in the answer. --live makes one
real call and reads prompt/output/reasoning counts off the response.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # pipeline/ imports are flat

from build_places import SOURCE, route_hours
from llm_hours_parser import PROMPT, PlaceHours, cache_key, get_client, load_hours_cache

# $ per 1M (input, output) tokens, short context.
# developers.openai.com/api/docs/pricing, Sep 2026. Sol's rate is promotional to Nov 21.
PRICES = {
    "gpt-5.6-sol": (4.00, 20.00),  # what llm_hours_parser.py asks for today
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
# Only these bill hidden reasoning tokens; the 4o models return the answer and nothing else.
REASONING_MODELS = {"gpt-5.6-sol", "gpt-5.6-luna"}

# The JSON schema for PlaceHours rides along on every call as input tokens.
SCHEMA = json.dumps(PlaceHours.model_json_schema())
CHAT_OVERHEAD = 10  # role and formatting tokens the API wraps the message in


def tokens(text: str) -> int:
    return round(len(text) / 4)


def prompt_for(hours, notes) -> str:
    return PROMPT.format(hours=hours if hours else "(none)", seasonal_notes=notes or "")


def as_model_output(entry: dict) -> str:
    """A cached answer back in the shape the model emitted it, so it can be counted."""
    return json.dumps(
        {
            "open_days": [
                {"day": day, "hours": day_hours["hours"], "weeks": day_hours["weeks"]}
                for day, day_hours in entry["open_days"].items()
            ],
            "open_months": entry["open_months"],
        },
        separators=(",", ":"),
    )


def llm_calls() -> tuple[int, dict]:
    """Row count, and every distinct (hours, notes) pair the LLM has to read."""
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    pending = {}
    for row in rows:
        row = {**row, "posted_hours": row["hours"]}  # clean_frame renames it
        if route_hours(row)[0] == "llm":
            key = cache_key(row["posted_hours"], row["seasonal_notes"])
            pending[key] = (row["posted_hours"], row["seasonal_notes"])
    return len(rows), pending


def measure(hours, notes, model: str) -> tuple[int, int, int | None]:
    """One real call: billed (prompt, completion, reasoning) tokens for these hours.

    reasoning is None when the API does not break it out, in which case whatever
    reasoning happened is already inside completion.
    """
    usage = (
        get_client()
        .chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": prompt_for(hours, notes)}],
            response_format=PlaceHours,
        )
        .usage
    )
    reasoning = getattr(usage.completion_tokens_details, "reasoning_tokens", None)
    return usage.prompt_tokens, usage.completion_tokens, reasoning


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reasoning", type=int, default=400, help="assumed reasoning tokens per call"
    )
    parser.add_argument("--live", action="store_true", help="measure with one real call")
    parser.add_argument("--model", default="gpt-5.6-sol", help="model for --live")
    args = parser.parse_args()

    places, pending = llm_calls()
    calls = len(pending)
    cache = load_hours_cache()

    in_tokens = sum(
        tokens(prompt_for(*pair)) + tokens(SCHEMA) + CHAT_OVERHEAD
        for pair in pending.values()
    )
    answers = {key: tokens(as_model_output(cache[key])) for key in pending if key in cache}
    answer_tokens = round(sum(answers.values()) / len(answers) * calls) if answers else 0
    # Offline: reasoning is guesswork, and only the gpt-5.6 models are billed for it.
    extra = {model: args.reasoning * calls for model in REASONING_MODELS}
    basis = f"reasoning assumed at {args.reasoning}/call, gpt-5.6 models only"

    print(f"{places} places -> {calls} LLM calls on a cold build ({len(answers)} cached to read)")

    if args.live:
        key, (hours, notes) = next(iter(pending.items()))
        live_in, live_out, live_reasoning = measure(hours, notes, args.model)
        sample_in = tokens(prompt_for(hours, notes)) + tokens(SCHEMA) + CHAT_OVERHEAD
        print(f"\nlive call on {hours!r} ({args.model}):")
        print(f"  input      {live_in} billed vs {sample_in} estimated")
        print(f"  completion {live_out} billed vs {answers[key]} estimated as answer text")
        print(f"  reasoning  {live_reasoning if live_reasoning is not None else 'not broken out'}")
        # Scale by the measured ratios rather than pinning every call to this one row:
        # the probe row is one of the larger answers.
        in_tokens = round(in_tokens * live_in / sample_in)
        answer_tokens = round(answer_tokens * live_out / answers[key])
        extra = dict.fromkeys(PRICES, 0)
        basis = "completion measured, so any reasoning is already inside it"

    print(f"\nper call: {round(in_tokens / calls):,} in, {round(answer_tokens / calls):,} out")
    print(f"totals:   {in_tokens:,} in, {answer_tokens:,} out")
    print(f"{basis}\n")

    for model, (price_in, price_out) in PRICES.items():
        billed_out = answer_tokens + extra.get(model, 0)
        cost = (in_tokens * price_in + billed_out * price_out) / 1_000_000
        print(f"  {model:<13} ${cost:.4f}")


if __name__ == "__main__":
    main()
