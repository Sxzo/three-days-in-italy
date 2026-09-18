# How I Approached Building My Submission for 3 Days in Italy

*Benvenuto and benvenuta!*

My project is a small planner that turns the messy 103-place catalog provided in `italy.json` into a structured dataset that's used to deterministically produce a fun-filled three-day trip. A user enters a starting city, starting date, and what they're into, and gets back an hour-by-hour itinerary that factors in all of these preferences plus travel time, opening hours, and getting home comfortably at the end of each day.

**Live:** https://three-days-in-italy-three.vercel.app  
**Code:** https://github.com/Sxzo/three-days-in-italy

---

## Starting with the data: turning italy.json into a structured dataset

A quick exploration of the provided dataset made it clear that whoever wrote this problem at Stripe had no intention of making it easy to parse. `italy.json` is messy: nine places have no duration, one has no `booking_required` value, tags are spelled both `local_favorite` and `local-favorite`, and messiest of all, values for `hours` are prose with no uniform structure. Going deeper, I found items such as the Vatican Museums with `seasonal_notes` like

> "Closed Sundays except last Sunday of the month..."

At that point it became apparent that there was inevitably going to be a need for some semantic parsing of unstructured text, which is a job perfectly suited for an LLM. The tradeoff question I asked myself was: how much of that data would the LLM have to process? Sure, the dataset is only [~22,000 tokens](https://platform.openai.com/tokenizer), but throwing the entire dataset at an LLM would add cost that grows with every row, and the risk of hallucination would scale with the number of calls and the amount of data passed to the model. To solve this, I built a hybrid deterministic / non-deterministic parsing pipeline, using regex and default assumptions wherever applicable and passing whatever was unparseable into a specific prompt for an LLM.

The result cut the LLM's share of the work to under 16% of places:

| Source | Count | How |
| --- | --- | --- |
| `posted` | 61 | Regex parser over the 8 string shapes that actually appear |
| `llm` | 16 | Prose the regex can't read, parsed once and cached in `data/hours_llm_cache.json` |
| `inferred` | 26 | Nothing posted, so a per-type default window fills in |

Alongside this, every item keeps an `hours_source` field to track where its hours came from. Other missing fields, like duration, were given type-based defaults rather than dropping any place from the already small 103-item list.

This process resulted in a low-cost, reliable, and easily testable data processing pipeline. Since this step lives offline (pre-build), I added tests for schema validation and for some of the trickier outputs the LLM had to parse (like the Vatican, above). The live server just loads a prebuilt JSON file in a few milliseconds: no LLM call, no network, no API key, and nothing that can behave differently between deploys. That keeps cold starts cheap on serverless hosting, and the underlying dataset is always diffable and revertable through the Git repo.

## How the planner works

This data processing leaves the planner with a much cleaner question to answer: given 103 places that all have a uniform structure, how do we fit them into a 3-day itinerary? First, there were a few assumptions I made:

1. Travel time from A to B is estimated from the haversine distance, inflated 1.25x to account for winding roads, at a blended movement speed of 22 mph. An estimate to avoid the added complexity of live routing.
2. City variety is encouraged by preferring places outside the starting city on day 2, whenever one can be reached and returned from within the day. A trip is always more fun with a bit more exploration.
3. Round trips only. For v1, every day starts at 9:00 in the starting city and gets you back there by 22:30. I swept 91 start/end windows across ~13,600 generated itineraries to pick these: most places open at 9:00, and 22:30 is the earliest end that fits every dinner service in the dataset plus the trip home.
4. No repeats, matched by coordinates; visiting the Trevi Fountain in the day means its separate "at night" entry won't appear later.
5. At most one restaurant per meal, per day. Without this cap, evenings filled with back-to-back dinners, since restaurants are all that's open once the sights close.

With these assumptions in place, the planner becomes a small simulation that walks the trip forward in time, starting from the landing city. It carries exactly two pieces of state, where you're standing and what the time is, and every item chosen is a product of this state plus the initial user inputs. The algorithm is greedy and makes its next choice based on the highest-scoring candidate that still leaves time to get home.

Scoring a candidate works as follows:

```
  + 1.0 x (rating - 4.5)
  + 2.0 per matched interest
  - 1.0 per price level over your budget
  - 1.0 per hour of added route time
  - 0.5 per hour spent waiting for the doors to open
  - 0.5 if the hours are unconfirmed
```

Rating is measured against 4.5, roughly the dataset average, so the term reads as "better or worse than a typical place." Ties break toward the stop with the earliest end time, then the higher rating. As a result, this algorithm rewards preferences while penalizing waiting, travel time, and uncertainty. The final product is deterministic, easy to tweak, and runs in about 10 ms with zero marginal cost.

I heavily considered using an LLM with high temperature to add randomness and judgment-like subjectivity, but chose to exclude it. Planning is constraint arithmetic (opening hours, travel time, getting home on time), which is exactly where LLMs are unreliable, and it would have brought a marginal cost on every request, black-box itineraries, limited control over inputs, and rate-limit requirements to keep the site (and my OpenAI wallet balance) safe on the open internet.

## UI, UX, and stack decisions

For these three elements of the project, I put a strong emphasis on simplicity, minimalism, and extensibility. Flask serves two HTML pages, a planner and a map, out of Jinja templates and native CSS, with htmx swapping in the itinerary. Because every filter lives in the query string and planning is deterministic, the URL is the trip, so any itinerary can be shared with a link. Given that the client holds almost no state, a framework like React would have been overkill for little gain.

## What I'd do with more time

This project was scoped to "a few hours of work," so it has real limits. Here's what I'd want to work on next:

- Real travel times. The 22 mph estimate makes intercity trips look far longer than a train ride, so a Rome trip never leaves Rome on day 2.
- Meal-aware planning. The cap prevents two dinners in a night but doesn't guarantee one, and cities like Bologna and Milan only have one or two dinner spots in the dataset.
- Enriching the dataset with live data like images of places, flight times, etc.
- Refining the itinerary algorithm to be more modular, with weights each end user can adjust
- Adding drag-and-drop customization on the itinerary itself
- Experimenting with LLM applications like a chat sidebar for questions about the entire dataset, natural-language trip summaries, and conversational replanning

## How I used AI during this project

In development, I used Cursor as my IDE throughout and leveraged Claude models as thinking partners to help brainstorm, reason about ideas, and test quickly to inform my design decisions. The majority of the code in this repository is not handwritten, though I manually reviewed every function.

In the product itself, there's exactly one LLM use case, and it never runs on the live server. 
I considered more, such as turning a user's written preferences into filters or having the LLM turn preferences into itineraries directly. 
I ultimately decided against these approaches because I found functional deterministic solutions that resulted in faster runtimes, lower costs, and much better observability.

## In summary
The thread through every decision here was putting the LLM only where it was needed. It reads the 16 places whose hours and seasonal notes were not easily parsable, once, offline, and protected behind validation tests. 
Everything a user touches is deterministic: the same inputs always produce the same trip, and every choice traces back to a score. 
The platform leverages what LLMs are good at, like reading messy prose, while avoiding the use cases where the downsides outweigh the benefits. Thanks for taking the time to read and review this, and I'm looking forward to extending it together!

Lev