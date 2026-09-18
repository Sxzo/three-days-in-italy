# 3 Days in Italy: How I Approached It

*Benvenuto and benvenuta!*

I built a small planner that turns the messy 103-place catalog in `italy.json` into a structured dataset, then deterministically produces a three-day trip from it. You enter a starting city, a date, and what you're into, and get back an hour-by-hour itinerary that accounts for travel time, opening hours, and getting home each night.

**Live:** https://three-days-in-italy-three.vercel.app  
**Code:** https://github.com/Sxzo/three-days-in-italy  
**Run locally:** [see the bottom of this page](#run-it-locally)

## The data

Whoever wrote this problem at Stripe had no intention of making it easy to parse. Nine places have no duration, tags are spelled both `local_favorite` and `local-favorite`, and `hours` is free prose, with notes like the Vatican's "Closed Sundays except last Sunday of the month."

Reading that kind of text is a job for an LLM. The question was how much of the data it should touch, since cost grows with every row and hallucination risk with every call. To account for this, I built a hybrid pipeline: regex and defaults wherever they work, and a narrow LLM prompt only for what can't be simply parsed.

| Source | Count | How |
| --- | --- | --- |
| `posted` | 61 | Regex parser over the 8 string shapes that actually appear |
| `llm` | 16 | Prose the regex can't read, parsed once and cached |
| `inferred` | 26 | Nothing posted, so a per-type default window fills in |

This all runs offline, behind tests for the schema and the trickier LLM outputs. The live server just loads a prebuilt JSON file: no LLM call, no outbound requests, no API key, nothing that can differ between deploys.

## The planner

The planner is a greedy simulation that walks the trip forward in time. It carries two pieces of state, where you are and what time it is, and repeatedly picks the highest-scoring stop that still leaves time to get home:

```
  + 1.0 x (rating - 4.5)
  + 2.0 per matched interest
  - 1.0 per price level over your budget
  - 1.0 per hour of added route time
  - 0.5 per hour spent waiting for the doors to open
  - 0.5 if the hours are unconfirmed
```

The assumptions behind it:

- Travel time is haversine distance, inflated 1.25x, at 22 mph.
- Every day is a round trip from the starting city, 9:00 to 22:30. I chose that window after sweeping 90+ start/end combinations: most places open at 9:00, and 22:30 is the earliest end that fits every dinner in the dataset.
- Day 2 prefers places outside the starting city, for variety.
- No place repeats, and each day allows one restaurant per meal. Without that cap, evenings filled with back-to-back dinners.

I seriously considered letting an LLM build the itinerary. I decided against it because planning is constraint arithmetic (hours, travel, getting home on time), which is exactly where LLMs are unreliable. It would also have added per-request cost, black-box results, and rate limiting to protect the site (and my OpenAI wallet). The deterministic version runs in about 10 ms, and every choice traces back to a score.

The stack is Flask, Jinja, htmx, and plain CSS. Filters live in the query string, so the URL is the trip and any itinerary is shareable. With almost no client state, React would have been overkill.

## With more time

- LLM features where they fit: conversational replanning, trip summaries, and a chat sidebar over the dataset
- Real travel times. The 22 mph estimate is why a Rome trip never leaves Rome on day 2.
- Live data: images, flight times
- User-adjustable scoring weights, and dragging stops between days

## How I used AI

As a building tool, I used Cursor throughout, with Claude models as thinking partners for brainstorming and quick tests. Most of the code is not handwritten, though I reviewed every function.

As a product feature, the LLM does exactly one job: it reads the 16 places whose hours only a human could parse, once, offline, behind tests. Everything a user touches is deterministic.

Thanks for reading, and I'm looking forward to extending it together!

Lev

---

## Run it locally

```
pip install -r requirements-dev.txt
python server/main.py                # http://localhost:5000
python -m pytest                     # 32 tests
python pipeline/build_places.py      # optional: rebuild the dataset
```

The rebuild is served from the LLM cache, so it only needs `OPENAI_API_KEY` (see `.env.example`) if `italy.json` gains new hour strings. To run just the app, `requirements.txt` is enough.
