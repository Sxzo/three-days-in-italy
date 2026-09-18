"""Stand-in values for the rows italy.json leaves blank.

These are judgement calls, not data. They exist so a place with a missing field
can still be scheduled instead of being dropped from the trip entirely; any row
that lands here is tagged hours_source="inferred" and shown to the user as
"Hours unconfirmed".
"""


def get_default_duration(place_type: str) -> int:
    # Only 9 of the 103 rows lack a duration, and today they are all viewpoints
    # bar one historic site. The branch is on type rather than a single constant
    # because a viewpoint is a quick stop and most other types are not.
    if place_type == 'viewpoint':
        return 30
    else: # historic site, etc.
        return 60

def get_default_hours_fields(place_type: str) -> dict:
    # Produces open_days, so it follows the same contract as llm_hours_parser.py:
    # a missing day is closed, a day with an empty `hours` list is open at an unknown time.
    # These defaults always state times, so they never emit an empty list.
    #
    # Everything here is open every day of every week and month; the only
    # question is the window. Neighborhoods and viewpoints are streets and
    # lookouts you can reach at any hour, parks tend to lock their gates around
    # dusk, and the rest get conservative daytime hours so the planner does not
    # promise an evening visit it cannot back up.
    if place_type in {'neighborhood', 'viewpoint'}:
        opening, closing = '00:00', '24:00'
    elif place_type == 'park':
        opening, closing = '08:00', '20:00'
    else:
        opening, closing = '09:00', '18:00'

    return {
        'open_days': {
            day: {
                'hours': [{'open': opening, 'close': closing}],
                'weeks': [1, 2, 3, 4, 5, -1],
            }
            for day in range(7)
        },
        'open_months': list(range(1, 13)),
    }