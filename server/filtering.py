"""The shared filter vocabulary: what the UI chips mean and which places clear them.

Both the chips in index.html and the planner in itinerary.py resolve preferences
through this module, so a chip can never mean one thing on screen and another
during planning.

Two kinds of constraint live here and the distinction is load-bearing. Excludes
and trip dates are hard: is_eligible() removes those places outright. Includes
and budget are soft and only reach the score in itinerary.py, because a user who
asks for wine and quiet should get a ranked trip, not an empty one.
"""

from calendar import monthrange
from datetime import datetime, timedelta


TRIP_LENGTH_DAYS = 3

# The dataset's 30 tags rolled up into the chips the UI offers. Each value is a list
# of raw tokens matched against a place's tags or its type.
GROUPS = {
    'history': ['historic', 'historic_site'],
    'art': ['art', 'modern', 'museum'],
    'food': ['food', 'market'],
    'wine': ['wine'],
    'outdoors': ['outdoors', 'active', 'park'],
    'views': ['views', 'photogenic', 'viewpoint'],
    'hidden': ['hidden-gem'],
    'iconic': ['iconic', 'tourist-heavy'],
    'quiet': ['quiet', 'relaxing', 'romantic'],
}

# The chips index.html renders, as (value, label). Values are GROUPS keys, or any raw
# tag or derived attribute matches_filter understands. The two sides differ on purpose:
# not every group is worth avoiding, and some read better with a different label.
FILTERS = {
    'include': [
        ('history', 'History'),
        ('art', 'Art & museums'),
        ('food', 'Food'),
        ('wine', 'Wine'),
        ('views', 'Views'),
        ('outdoors', 'Outdoors'),
        ('hidden', 'Hidden gems'),
        ('quiet', 'Quiet'),
    ],
    'exclude': [
        ('iconic', 'Crowds'),
        ('art', 'Museums'),
        ('wine', 'Wine'),
        ('outdoors', 'Active outings'),
        ('splurge', 'Splurges'),
        ('booking-required', 'Booking ahead'),
        ('long-visit', 'Long visits'),
    ],
}


class Preferences:
    """One request's filter selections, parsed once and shared by every endpoint.

    Excludes and trip dates are hard constraints. Includes and budget are soft:
    they rank what is left without emptying the itinerary.
    """

    def __init__(self, args):
        self.start_day = parse_start_day(args.get('start_day', ''))
        self.trip_days = trip_days(self.start_day)
        self.includes = args.getlist('include')
        self.excludes = args.getlist('exclude')
        self.max_budget = parse_max_budget(args.get('max_budget', ''))


def parse_start_day(value):
    """Return the trip's first day, or None when it cannot begin a trip.

    That covers missing and malformed values, and the handful of dates too near
    date.max to hold three days: trip_days() would raise OverflowError on those
    rather than return a short trip.
    """
    if not value:
        return None
    try:
        start_day = datetime.strptime(value, '%Y-%m-%d').date()
        start_day + timedelta(days=TRIP_LENGTH_DAYS - 1)
    except (ValueError, OverflowError):
        return None
    return start_day


def parse_max_budget(value):
    """Return the selected price level, or None when no budget chip is set."""
    try:
        return int(value)
    except ValueError:
        return None


def trip_days(start_day):
    """The dates the trip covers. Empty when no usable start day was given."""
    if start_day is None:
        return []
    return [start_day + timedelta(days=offset) for offset in range(TRIP_LENGTH_DAYS)]


def matches_filter(place, value):
    """Match a UI filter to a preference group or a derived place attribute."""
    if value == 'booking-required':
        return place['booking_required']
    if value == 'long-visit':
        return place['duration_minutes'] >= 180
    return any(
        token in place['tags'] or token == place['type']
        for token in GROUPS.get(value, [value])
    )


def preference_matches(place, includes):
    """How many of the "looking for" chips a place satisfies."""
    return sum(matches_filter(place, value) for value in includes)


# Check numbered weeks such as "first Monday" and the special -1 value used by
# the dataset for the last occurrence of a weekday in the month.
def _week_matches(trip_day, weeks):
    week_of_month = (trip_day.day - 1) // 7 + 1
    is_last_week = trip_day.day + 7 > monthrange(trip_day.year, trip_day.month)[1]
    return week_of_month in weeks or (-1 in weeks and is_last_week)


def schedule_for(place, trip_day):
    """The place's schedule for that date, or None when it is closed.

    Availability is month, weekday, and week-of-month; the planner reads the
    returned schedule's posted hours to place the visit.
    """
    dataset_weekday = (trip_day.weekday() + 1) % 7  # Dataset uses Sunday = 0.
    schedule = place['open_days'].get(dataset_weekday)
    if schedule is None or trip_day.month not in place['open_months']:
        return None
    if not _week_matches(trip_day, schedule['weeks']):
        return None
    return schedule


def is_open_on(place, trip_day):
    return schedule_for(place, trip_day) is not None


def is_open_during_trip(place, days):
    """True when a place is available on at least one day of the trip."""
    if not days:
        return True
    return any(is_open_on(place, trip_day) for trip_day in days)


def is_eligible(place, preferences):
    """Every hard constraint a place must clear before it can be planned."""
    return (
        is_open_during_trip(place, preferences.trip_days)
        and not any(matches_filter(place, value) for value in preferences.excludes)
    )


def filter_places(places, preferences):
    return [place for place in places.values() if is_eligible(place, preferences)]
