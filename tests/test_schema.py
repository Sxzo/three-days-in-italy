"""Schema validation for data/places.build.json, the artifact the app serves.

server/main.py parses nothing at startup and has no fallback, so a bad artifact
is a bad deploy. Two checks: every field is the type and range the app assumes,
and every row carries opening hours the planner can actually schedule.
"""

import re

# The spec, as the app relies on it. Every field is required and non-null.
FIELD_TYPES = {
    'id': str, 'name': str, 'type': str, 'city': str, 'region': str,
    'latitude': float, 'longitude': float, 'duration_minutes': int,
    'rating': float, 'booking_required': bool, 'tags': list,
    'open_days': dict, 'open_months': list, 'hours_source': str,
}
# Also required, but null wherever italy.json had nothing to say.
NULLABLE_FIELDS = {'neighborhood', 'description', 'posted_hours', 'seasonal_notes', 'price_range'}

PRICE_RANGES = {'€', '€€', '€€€', '€€€€'}
HOURS_SOURCES = {'posted', 'llm', 'inferred'}
VALID_WEEKS = {1, 2, 3, 4, 5, -1}
HHMM = re.compile(r'^\d{2}:\d{2}$')
# Loose enough to allow new rows, tight enough to catch a swapped lat/long pair.
LATITUDE_BOUNDS = (35.0, 47.5)
LONGITUDE_BOUNDS = (6.0, 19.0)


def to_minutes(hhmm):
    """'09:30' -> 570. Values past 24:00 mean the next morning ('25:00')."""
    hours, minutes = (int(part) for part in hhmm.split(':'))
    return hours * 60 + minutes


def test_every_row_matches_the_field_spec(built_places):
    assert built_places, 'the artifact is empty'
    for place in built_places:
        label = place.get('id')
        # An exact match, not a subset: a missing field breaks the app, and an
        # unexpected one usually means the build changed without the app knowing.
        assert set(place) == set(FIELD_TYPES) | NULLABLE_FIELDS, f'{label}: field set changed'
        for field, field_type in FIELD_TYPES.items():
            assert isinstance(place[field], field_type), f'{label}: {field} is not {field_type}'

        assert LATITUDE_BOUNDS[0] <= place['latitude'] <= LATITUDE_BOUNDS[1], label
        assert LONGITUDE_BOUNDS[0] <= place['longitude'] <= LONGITUDE_BOUNDS[1], label
        assert 0 < place['duration_minutes'] <= 600, f'{label}: implausible duration'
        assert 0 <= place['rating'] <= 5, f'{label}: rating off the 5-point scale'
        # The planner reads budget as len(price_range), so only € runs work.
        assert place['price_range'] in PRICE_RANGES, f'{label}: odd price_range'
        assert place['hours_source'] in HOURS_SOURCES, f'{label}: unknown hours_source'
        # The build settles italy.json's local_favorite/local-favorite split.
        assert all(tag and '_' not in tag for tag in place['tags']), f'{label}: un-normalised tag'

    ids = [place['id'] for place in built_places]
    assert len(set(ids)) == len(ids), 'duplicate id would collapse on load'


def test_every_row_can_actually_be_scheduled(built_places):
    """The open_days contract, which all three hours producers have to honour.

    A weekday missing from open_days is CLOSED. A weekday present with an empty
    `hours` list is OPEN at an unknown time -- never closed, never 00:00.
    """
    for place in built_places:
        assert place['open_days'], f"{place['id']}: never open, so never plannable"
        assert set(place['open_months']) <= set(range(1, 13)), f"{place['id']}: bad month"
        assert place['open_months'], f"{place['id']}: open in no month"

        for day, schedule in place['open_days'].items():
            where = f"{place['id']} day {day}"
            # Weekday keys are strings in the JSON file and ints once the app
            # loads it, so compare as ints. 0 is Sunday, not Monday.
            assert int(day) in range(7), f'{where}: weekday outside 0-6'
            assert schedule['weeks'], f'{where}: empty weeks means never open'
            assert set(schedule['weeks']) <= VALID_WEEKS, f'{where}: unknown week number'
            for window in schedule['hours']:
                opens, closes = window['open'], window['close']
                assert HHMM.match(opens) and HHMM.match(closes), f'{where}: not HH:MM'
                # A close may run past midnight ('25:00'), never before opening.
                assert to_minutes(opens) < to_minutes(closes) <= 48 * 60, f'{where}: bad window'
