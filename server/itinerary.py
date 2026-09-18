"""Turn a filtered set of places into a three-day, hour-by-hour itinerary.

The planner is greedy rather than optimal: each day starts at the hub and
repeatedly appends the highest-scoring stop that still leaves time to get back,
until nothing fits. A greedy pass is easy to explain to a user ("we picked the
best next thing") and fast enough to run on every keystroke, which matters more
here than squeezing out a marginally shorter route.

Scoring lives in candidate_option(); the hard constraints it cannot override
live in filtering.py.
"""

from datetime import datetime, time, timedelta
from math import asin, ceil, cos, radians, sin, sqrt

from filtering import Preferences, filter_places, preference_matches, schedule_for


# The planner's own day, not any place's opening hours. Every day begins and
# must end back at the hub inside this window; it also stands in as the assumed
# window for places whose real hours are unknown.
DAY_START = time(9, 0)
DAY_END = time(20, 0)

# A deliberately simple travel estimate until a routing service is introduced.
# Haversine distance is inflated to approximate a road route, then converted to
# time at a blended city/regional speed.
ROAD_DISTANCE_FACTOR = 1.25
AVERAGE_TRAVEL_SPEED_MPH = 22
MIN_TRAVEL_MINUTES = 5

PREFERENCE_WEIGHT = 2.0
BUDGET_LEVEL_PENALTY = 1.0
TRAVEL_HOUR_PENALTY = 1.0
WAIT_HOUR_PENALTY = 0.5
UNCONFIRMED_HOURS_PENALTY = 0.5


# Calculate straight-line distance with the Haversine formula. The destination
# can be either a place dictionary or a (latitude, longitude) pair.
def distance_miles(origin, place):
    lat1, lon1 = origin
    if isinstance(place, dict):
        lat2, lon2 = place['latitude'], place['longitude']
    else:
        lat2, lon2 = place
    lat_delta = radians(lat2 - lat1)
    lon_delta = radians(lon2 - lon1)
    value = (
        sin(lat_delta / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(lon_delta / 2) ** 2
    )
    return 3958.8 * 2 * asin(sqrt(value))


# Convert distance into a simple travel-time estimate, including the road
# distance adjustment and a minimum for short trips.
def travel_minutes(origin, destination):
    distance = distance_miles(origin, destination)
    if distance < 0.1:
        return 0
    estimated = distance * ROAD_DISTANCE_FACTOR / AVERAGE_TRAVEL_SPEED_MPH * 60
    return max(MIN_TRAVEL_MINUTES, ceil(estimated))


# Estimate actual route mileage by expanding the straight-line distance.
def travel_miles(origin, destination):
    return distance_miles(origin, destination) * ROAD_DISTANCE_FACTOR


# Convert dataset times into datetimes. Values beyond 24:00 represent times
# after midnight, such as 25:00 meaning 01:00 the following day.
def _parse_time(trip_day, value):
    hours, minutes = (int(part) for part in value.split(':'))
    day_offset, hour = divmod(hours, 24)
    return datetime.combine(
        trip_day + timedelta(days=day_offset),
        time(hour, minutes),
    )


# Find the earliest visit that fits both the posted hours and planner day.
# Empty or inferred hours use the full planner day and are flagged as unconfirmed.
def visit_window(place, trip_day, earliest_start, day_end):
    schedule = schedule_for(place, trip_day)
    if schedule is None:
        return None

    posted_windows = schedule['hours']
    hours_unconfirmed = place.get('hours_source') == 'inferred' or not posted_windows
    if not posted_windows:
        posted_windows = [
            {
                'open': DAY_START.strftime('%H:%M'),
                'close': DAY_END.strftime('%H:%M'),
            }
        ]

    duration = timedelta(minutes=place['duration_minutes'])
    for posted in posted_windows:
        opens_at = _parse_time(trip_day, posted['open'])
        closes_at = _parse_time(trip_day, posted['close'])
        starts_at = max(earliest_start, opens_at)
        ends_at = starts_at + duration
        if ends_at <= min(closes_at, day_end):
            return starts_at, ends_at, hours_unconfirmed
    return None


# Build and score one possible next stop. Reject it if its visit and return trip
# cannot finish before day-end; keep unconfirmed hours with a scoring penalty.
def candidate_option(
    place,
    trip_day,
    current_time,
    current_point,
    hub_coordinates,
    includes,
    max_budget,
    day_end,
):
    outbound_minutes = travel_minutes(current_point, place)
    outbound_miles = travel_miles(current_point, place)
    arrival_time = current_time + timedelta(minutes=outbound_minutes)
    window = visit_window(place, trip_day, arrival_time, day_end)
    if window is None:
        return None

    starts_at, ends_at, hours_unconfirmed = window
    return_minutes = travel_minutes(
        (place['latitude'], place['longitude']),
        hub_coordinates,
    )
    if ends_at + timedelta(minutes=return_minutes) > day_end:
        return None

    wait_minutes = int((starts_at - arrival_time).total_seconds() // 60)
    current_return_minutes = travel_minutes(current_point, hub_coordinates)
    added_route_minutes = outbound_minutes + return_minutes - current_return_minutes
    matched_preferences = preference_matches(place, includes)
    price_level = len(place['price_range']) if place['price_range'] else 0
    over_budget_levels = (
        max(0, price_level - max_budget) if max_budget is not None else 0
    )

    # Higher scores are better. Rating is the baseline, preference matches add
    # value, while exceeding the budget preference, extra route time, waiting,
    # and unconfirmed hours reduce it.
    # With the current weights, a confirmed 4.6-rated place matching one
    # preference, adding 30 minutes of travel and 20 minutes of waiting scores
    # 5.93. If its hours were unconfirmed, another 0.5 would make the score 5.43:
    # 4.6 + (1 * 2.0) - (0.5 * 1.0) - (0.33 * 0.5) = 5.93
    # 5.93 - (1 * 0.5) = 5.43
    score = (
        place['rating']
        + matched_preferences * PREFERENCE_WEIGHT
        - over_budget_levels * BUDGET_LEVEL_PENALTY
        - added_route_minutes / 60 * TRAVEL_HOUR_PENALTY
        - wait_minutes / 60 * WAIT_HOUR_PENALTY
        - hours_unconfirmed * UNCONFIRMED_HOURS_PENALTY
    )
    return {
        'place': place,
        'starts_at': starts_at,
        'ends_at': ends_at,
        'travel_minutes': outbound_minutes,
        'travel_miles': outbound_miles,
        'wait_minutes': wait_minutes,
        'hours_unconfirmed': hours_unconfirmed,
        'return_minutes': return_minutes,
        'score': score,
    }


# Greedily build three daily routes. Each iteration chooses the highest-scoring
# feasible next stop, and every day begins and ends at the selected hub.
def get_itinerary(places, args):
    start_hub = args.get('hub', '')
    # Every day is a round trip, so the two are the same city today. They are
    # kept as separate fields because the templates already read them that way,
    # and a one-way trip would only need to change this line.
    end_hub = start_hub
    preferences = Preferences(args)
    includes = preferences.includes

    if not start_hub or not args.get('start_day', ''):
        raise ValueError('hub and start_day are required')

    if preferences.start_day is None:
        raise ValueError('start_day must be a real date in YYYY-MM-DD format')

    first_day = preferences.start_day
    hub_places = [place for place in places.values() if place['city'] == start_hub]
    if not hub_places:
        raise ValueError('hub must match a city in the places dataset')

    # The dataset has no coordinate for a city, only for places, so the centroid
    # of a city's places stands in for "where the day starts and ends".
    hub_coordinates = (
        sum(place['latitude'] for place in hub_places) / len(hub_places),
        sum(place['longitude'] for place in hub_places) / len(hub_places),
    )
    # Same hard constraints the place cards use, so the planner never considers a
    # place the user filtered away.
    remaining = filter_places(places, preferences)

    days = []
    for offset, trip_day in enumerate(preferences.trip_days):
        current_point = hub_coordinates
        current_time = datetime.combine(trip_day, DAY_START)
        day_end = datetime.combine(trip_day, DAY_END)
        day_places = []
        visit_minutes = 0
        travel_total = 0
        travel_miles_total = 0

        while True:
            options = []

            # Day two is reserved for a day trip. Left alone, the scoring would
            # keep picking hub places all three days, because anything further
            # out pays a travel penalty it can rarely win back. Restricting the
            # candidates for one day is a blunt way to buy variety without
            # reweighting the score for the other two.
            if offset == 1:
                for place in [p for p in remaining if p['city'] != start_hub]:
                    option = candidate_option(
                        place,
                        trip_day,
                        current_time,
                        current_point,
                        hub_coordinates,
                        includes,
                        preferences.max_budget,
                        day_end,
                    )
                    if option is not None:
                        options.append(option)

            # Fall back to the unrestricted set. This covers day one and three,
            # and day two once the out-of-hub places are used up or too far to
            # reach and still return by DAY_END. Rome is the case worth knowing:
            # nothing outside it is reachable and back within the day, so its
            # day two quietly stays in the city rather than failing.
            if len(options) == 0:
                for place in remaining:
                    option = candidate_option(
                        place,
                        trip_day,
                        current_time,
                        current_point,
                        hub_coordinates,
                        includes,
                        preferences.max_budget,
                        day_end,
                    )
                    if option is not None:
                        options.append(option)
            
            if not options:
                break

            # Break ties toward the stop that frees the day up soonest, then the
            # better-rated one. Without the earliest-finish term, equally scored
            # options are settled by dataset order, which makes the same inputs
            # look arbitrary from one place to the next.
            selected = max(
                options,
                key=lambda option: (
                    option['score'],
                    # Offset from datetime.min rather than .timestamp(), which
                    # goes through the platform clock and raises OSError on
                    # Windows for any date before 1970.
                    -(option['ends_at'] - datetime.min).total_seconds(),
                    option['place']['rating'],
                ),
            )
            place = selected['place']
            day_places.append(
                {
                    **place,
                    'start_time': selected['starts_at'].strftime('%H:%M'),
                    'end_time': selected['ends_at'].strftime('%H:%M'),
                    'travel_from_previous_minutes': selected['travel_minutes'],
                    'travel_from_previous_miles': round(selected['travel_miles'], 1),
                    'wait_minutes': selected['wait_minutes'],
                    'hours_unconfirmed': selected['hours_unconfirmed'],
                    'selection_score': round(selected['score'], 2),
                }
            )
            # `remaining` spans the whole trip, not just this day, so nothing is
            # ever scheduled twice across the three days. Dropping by coordinate
            # rather than by identity, because the dataset describes some spots
            # twice over -- the Trevi Fountain has a second row for seeing it at
            # night -- and those rows would otherwise read as two stops that
            # send the user back somewhere they have already been.
            current_point = (place['latitude'], place['longitude'])
            remaining = [
                other
                for other in remaining
                if (other['latitude'], other['longitude']) != current_point
            ]
            visit_minutes += place['duration_minutes']
            travel_total += selected['travel_minutes']
            travel_miles_total += selected['travel_miles']
            current_time = selected['ends_at']

        return_minutes = travel_minutes(current_point, hub_coordinates) if day_places else 0
        return_miles = travel_miles(current_point, hub_coordinates) if day_places else 0
        travel_total += return_minutes
        travel_miles_total += return_miles
        return_time = current_time + timedelta(minutes=return_minutes)

        days.append(
            {
                'date': trip_day.isoformat(),
                'display_date': f"{trip_day:%a, %b} {trip_day.day}",
                'places': day_places,
                'total_visit_minutes': visit_minutes,
                'total_travel_minutes': travel_total,
                'total_travel_miles': round(travel_miles_total, 1),
                'return_to_hub_minutes': return_minutes,
                'return_to_hub_miles': round(return_miles, 1),
                'return_time': return_time.strftime('%H:%M'),
            }
        )

    return {
        'hub': start_hub,
        'start_hub': start_hub,
        'end_hub': end_hub,
        'start_day': first_day.isoformat(),
        'days': days,
        'total_places': sum(len(day['places']) for day in days),
        'total_visit_minutes': sum(day['total_visit_minutes'] for day in days),
        'total_travel_minutes': sum(day['total_travel_minutes'] for day in days),
        'total_travel_miles': round(sum(day['total_travel_miles'] for day in days), 1),
    }