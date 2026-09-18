"""The critical path the site depends on: filtering, the planner, the endpoints.

The planner is greedy, not optimal, so nothing here asserts the best route.
What is asserted is that it never promises the impossible: no stop outside its
opening hours, no day that cannot get back to the hub, no place visited twice.
"""

import json
from datetime import date, datetime, time

import pytest
from filtering import Preferences, filter_places, is_open_on, matches_filter, schedule_for
from itinerary import DAY_END, DAY_START, candidate_option, get_itinerary, visit_window
from werkzeug.datastructures import MultiDict

# September 2026: the 18th is a Friday, the 20th the third Sunday, the 27th the last.
FRIDAY = date(2026, 9, 18)
SUNDAY = date(2026, 9, 20)
LAST_SUNDAY = date(2026, 9, 27)
COLOSSEUM = (41.8902, 12.4922)
QUERY = 'hub=Rome&start_day=2026-09-18'


def at(hour, minute=0):
    """A clock time on the trip's first day. The planner works in datetimes."""
    return datetime.combine(FRIDAY, time(hour, minute))


def args(**params):
    """Request args the way Flask hands them over, repeated keys included.

    A plain dict cannot hold include=food&include=wine, so pass a list for the
    chips: args(hub='Rome', include=['food', 'wine']).
    """
    pairs = []
    for key, value in params.items():
        values = value if isinstance(value, list) else [value]
        pairs.extend((key, item) for item in values)
    return MultiDict(pairs)


def to_minutes(hhmm):
    """'09:30' -> 570. Values past 24:00 mean the next morning ('25:00')."""
    hours, minutes = (int(part) for part in hhmm.split(':'))
    return hours * 60 + minutes


# --- filtering: what a chip means and when a place is open -----------------


def test_a_chip_matches_tags_types_and_derived_fields(make_place):
    # 'historic_site' is a type rather than a tag, but History must catch it.
    assert matches_filter(make_place(type='historic_site'), 'history')
    # Quiet rolls up quiet, relaxing and romantic.
    assert matches_filter(make_place(tags=['relaxing']), 'quiet')
    assert not matches_filter(make_place(tags=['tourist-heavy']), 'quiet')
    # Two chips read a field instead of the tag list.
    assert matches_filter(make_place(booking_required=True), 'booking-required')
    assert matches_filter(make_place(duration_minutes=180), 'long-visit')
    assert not matches_filter(make_place(duration_minutes=179), 'long-visit')


def test_opening_days_respect_weekday_week_and_month(make_place):
    # Each place below is open on exactly one weekday, day 0, so the assertions
    # isolate one rule at a time: the weekday number, then the week, then the month.
    #
    # Python's weekday() puts Monday at 0; the dataset puts Sunday there.
    sundays = make_place(open_days={0: {'hours': [], 'weeks': [1, 2, 3, 4, 5, -1]}})
    assert is_open_on(sundays, SUNDAY) and not is_open_on(sundays, FRIDAY)
    # An empty hours list is open at an unknown time, never closed.
    assert schedule_for(sundays, SUNDAY)['hours'] == []

    third_week = make_place(open_days={0: {'hours': [], 'weeks': [3]}})
    assert is_open_on(third_week, SUNDAY) and not is_open_on(third_week, LAST_SUNDAY)

    last_week = make_place(open_days={0: {'hours': [], 'weeks': [-1]}})
    assert is_open_on(last_week, LAST_SUNDAY) and not is_open_on(last_week, SUNDAY)

    assert not is_open_on(make_place(open_months=[10]), SUNDAY)


def test_excludes_are_hard_while_includes_and_budget_are_soft(make_place):
    preferences = Preferences(
        args(start_day='2026-09-18', exclude='wine', include='history', max_budget='1')
    )
    # Three candidates: one hits the exclude, one is shut in September, and one
    # is simply expensive and not what was asked for.
    candidates = {
        'wine': make_place(id='wine', tags=['wine']),
        'pricey': make_place(id='pricey', tags=['food'], price_range='€€€€'),
        'closed': make_place(id='closed', tags=['food'], open_months=[1]),
    }
    # Only the exclude and the trip dates remove anything. An expensive
    # non-match survives to be ranked, so preferences cannot empty a trip.
    assert [place['id'] for place in filter_places(candidates, preferences)] == ['pricey']


# --- the planner: fitting one stop, then a whole trip ----------------------


def test_a_visit_waits_for_opening_and_is_refused_when_it_cannot_finish(make_place):
    # visit_window returns (start, end, hours_unconfirmed); the slice drops the
    # flag, which the next test covers. The place opens at 09:00 and takes an
    # hour, so arriving at 08:00 means waiting rather than starting early.
    assert visit_window(make_place(), FRIDAY, at(8), at(22))[:2] == (at(9), at(10))
    assert visit_window(make_place(), FRIDAY, at(17, 30), at(22)) is None  # past the 18:00 close
    assert visit_window(make_place(), FRIDAY, at(9), at(9, 30)) is None  # past the day's end


def test_unknown_hours_are_planned_as_open_and_flagged_for_the_user(make_place):
    unknown = make_place(
        open_days={day: {'hours': [], 'weeks': [1, 2, 3, 4, 5, -1]} for day in range(7)}
    )
    starts_at, _, unconfirmed = visit_window(unknown, FRIDAY, at(8), at(22))
    # With no posted window the planner's own day stands in, and the stop is
    # shown as "Hours unconfirmed" rather than dropped or invented.
    assert starts_at == datetime.combine(FRIDAY, DAY_START) and unconfirmed is True


def test_a_stop_is_refused_when_the_trip_back_would_not_fit(make_place):
    # A degree of latitude is about 69 miles, so this sits roughly 60 miles out:
    # about 3.5 hours each way. Open around the clock, so nothing but the return
    # leg can rule it out.
    far = make_place(
        latitude=COLOSSEUM[0] + 0.87,
        open_days={
            day: {'hours': [{'open': '00:00', 'close': '24:00'}], 'weeks': [1, 2, 3, 4, 5, -1]}
            for day in range(7)
        },
    )

    # Same stop, same hub, only the time of day changes. The trailing arguments
    # are the empty include list, no budget, and the 22:00 end of the day.
    def option(current_time):
        return candidate_option(far, FRIDAY, current_time, COLOSSEUM, COLOSSEUM, [], None,
                                datetime.combine(FRIDAY, DAY_END))

    assert option(at(16)) is None  # reachable, but no way home by 22:00
    assert option(at(9)) is not None


def test_scoring_rewards_a_match_and_penalises_going_over_budget(make_place):
    # Both places sit on the hub itself, so travel and waiting score zero and
    # the difference between two runs is only the term being tested.
    def score(place, includes=(), max_budget=None):
        return candidate_option(place, FRIDAY, at(9), COLOSSEUM, COLOSSEUM, list(includes),
                                max_budget, datetime.combine(FRIDAY, DAY_END))['score']

    # One matched chip is worth PREFERENCE_WEIGHT.
    food = make_place(tags=['food'])
    assert score(food, includes=['food']) - score(food) == pytest.approx(2.0)
    # €€€ against a € budget is two levels over, at BUDGET_LEVEL_PENALTY each.
    pricey = make_place(price_range='€€€')
    assert score(pricey, max_budget=3) - score(pricey, max_budget=1) == pytest.approx(2.0)


@pytest.mark.parametrize(
    'params, message',
    [
        ({'hub': 'Rome'}, 'required'),
        ({'hub': 'Rome', 'start_day': '18-09-2026'}, 'YYYY-MM-DD'),
        ({'hub': 'Atlantis', 'start_day': '2026-09-18'}, 'must match a city'),
        # Too near date.max to hold three days, so it cannot begin a trip.
        ({'hub': 'Rome', 'start_day': '9999-12-31'}, 'YYYY-MM-DD'),
    ],
)
def test_unusable_input_raises_rather_than_returning_an_empty_trip(places, params, message):
    with pytest.raises(ValueError, match=message):
        get_itinerary(places, args(**params))


def test_a_trip_can_start_on_any_date_the_calendar_allows(places):
    # A date before 1970 is unusual but perfectly plannable. It is worth its own
    # test because ordering equally scored stops used to go through the platform
    # clock, which has no room for one and fails outright rather than sorting.
    result = get_itinerary(places, args(hub='Rome', start_day='1969-06-01'))
    assert result['total_places'] > 0


def test_one_location_is_never_scheduled_twice_under_two_names(places):
    # The dataset lists the Trevi Fountain twice, once for seeing it at night,
    # at coordinates identical to the daytime row. Deduping by id alone let both
    # into the same trip, which reads as being sent back to the same fountain.
    result = get_itinerary(places, args(hub='Rome', start_day='2026-09-18'))
    points = [
        (place['latitude'], place['longitude'])
        for day in result['days']
        for place in day['places']
    ]
    assert len(set(points)) == len(points)


def test_a_trip_never_breaks_its_own_rules(places):
    """Walk a real three-day trip and check the promises it makes to the user.

    One test rather than four because the invariants only mean anything
    together: stops in order, each inside its opening hours, home before the
    day ends, and nothing visited twice. Failures name the place.
    """
    result = get_itinerary(places, args(hub='Rome', start_day='2026-09-18'))
    assert [day['date'] for day in result['days']] == ['2026-09-18', '2026-09-19', '2026-09-20']

    visited = []
    for day in result['days']:
        trip_day = date.fromisoformat(day['date'])
        # A running clock in minutes since midnight, starting at the 09:00 the
        # planner begins each day with, then moved forward by each visit.
        clock = to_minutes(DAY_START.strftime('%H:%M'))
        for place in day['places']:
            visited.append(place['id'])
            start = to_minutes(place['start_time'])
            end = start + place['duration_minutes']
            assert start >= clock, f"{place['id']} starts before the previous stop ends"
            clock = end
            # Unconfirmed stops were planned against the assumed day rather
            # than posted hours, so there is nothing to compare them to.
            if not place['hours_unconfirmed']:
                # A place can post two windows in a day (lunch and dinner);
                # the visit has to fall entirely inside one of them.
                windows = schedule_for(places[place['id']], trip_day)['hours']
                assert any(
                    to_minutes(window['open']) <= start and end <= to_minutes(window['close'])
                    for window in windows
                ), f"{place['id']} planned outside its opening hours"
        # Every day is a round trip that has to be home before the day ends.
        assert to_minutes(day['return_time']) <= to_minutes(DAY_END.strftime('%H:%M'))

    assert len(set(visited)) == len(visited) == result['total_places']


def test_excluded_places_never_appear_in_a_trip(places):
    result = get_itinerary(
        places, args(hub='Rome', start_day='2026-09-18', exclude=['booking-required'])
    )
    planned = [place for day in result['days'] for place in day['places']]
    assert planned, 'the exclude emptied the trip'
    assert not any(place['booking_required'] for place in planned)


# --- the Flask layer -------------------------------------------------------


def test_load_places_keys_the_dataset_by_id_with_integer_weekdays(places, built_places):
    # The only processing between the file and the planner. JSON object keys
    # are strings, but every scheduling comparison downstream is int-keyed.
    assert len(places) == len(built_places)
    assert places['place_001']['name'] == 'Colosseum'
    assert all(isinstance(day, int) for day in places['place_001']['open_days'])


def test_the_planner_page_offers_the_busiest_cities_first(client):
    body = client.get('/').get_data(as_text=True)
    assert 'hx-get="/itinerary"' in body
    assert 'name="hub"' in body and 'name="start_day"' in body
    # Rome has the most places, so it leads the dropdown: a city with more
    # places can fill three days.
    assert body.index('value="Rome"') < body.index('value="Modena"')


def test_the_edit_flag_reopens_the_filters_without_losing_them(client):
    shared = client.get(f'/?{QUERY}&include=food').get_data(as_text=True)
    editing = client.get(f'/?{QUERY}&include=food&edit=1').get_data(as_text=True)

    # A shared link opens on the trip it describes. The body class is what
    # picks the view; the heading is the trip itself, rendered into the page.
    assert '<body class="show-itinerary">' in shared
    assert 'Three days around Rome' in shared
    # "Edit trip" puts that same link back on the form, which is what a reload
    # from there has to do too, rather than rebuilding the trip just left.
    assert '<body class="show-itinerary">' not in editing
    assert 'Three days around Rome' not in editing
    # The filters stay in the URL through all of it, so the form comes back
    # filled in rather than blank.
    assert 'data-city="Rome" selected' in editing
    assert 'name="include" value="food" checked' in editing


def test_the_itinerary_endpoint_answers_in_both_formats(client):
    payload = client.get(f'/itinerary?{QUERY}&include=food').get_json()
    fragment = client.get(f'/itinerary?{QUERY}&include=food&format=html').get_data(as_text=True)

    assert payload['hub'] == 'Rome' and len(payload['days']) == 3
    # A fragment, not a page: index.html swaps this into #results and looks for
    # the .itinerary class to tell a trip from an error.
    assert '<html' not in fragment and 'class="itinerary"' in fragment
    # The map link replays the same filters, so it cannot show a different trip.
    assert 'include=food' in fragment


def test_a_bad_request_fails_the_way_each_caller_needs(client):
    bad = 'hub=Atlantis&start_day=2026-09-18'
    assert client.get(f'/itinerary?{bad}').status_code == 400
    # Deliberately 200 for htmx, which does not swap a non-2xx response and
    # would otherwise leave the user staring at an unchanged page.
    fragment = client.get(f'/itinerary?{bad}&format=html')
    assert fragment.status_code == 200 and 'itinerary-error' in fragment.get_data(as_text=True)
    # The map is a whole page, so a browser navigating there gets a real status.
    assert client.get(f'/itinerary/map?{bad}').status_code == 400


@pytest.mark.parametrize('reserved', ['_anchor=x', '_method=POST', '_scheme=https', '_external=1'])
def test_url_for_options_in_the_query_string_are_not_forwarded(client, reserved):
    # url_for() reads leading-underscore keys as its own options, so replaying a
    # request's raw arguments let a query string steer URL building: three of
    # these returned a 500 and the fourth rewrote the link to an absolute URL.
    response = client.get(f'/itinerary?{QUERY}&format=html&{reserved}')
    assert response.status_code == 200
    # The trip is still described by the link; only the smuggled key is gone.
    assert response.headers['HX-Replace-Url'].startswith('/?hub=Rome')


def test_the_map_page_numbers_every_stop_across_the_whole_trip(client):
    total = client.get(f'/itinerary?{QUERY}').get_json()['total_places']
    body = client.get(f'/itinerary/map?{QUERY}').get_data(as_text=True)

    # map.html hands its stops to MapLibre as a JSON array in a <script> tag,
    # so the page is read the way the browser would: pull the text between
    # "const stops = " and the end of that statement, then parse it.
    stops = json.loads(body.split('const stops = ')[1].split(';\n')[0])
    # The sidebar and the pins both read this numbering, so it has to run 1..N
    # across the whole trip rather than restarting on each day.
    assert [stop['number'] for stop in stops] == list(range(1, total + 1))
    # And the days have to stay in order, or stop 5 could land on day one.
    assert [stop['day_number'] for stop in stops] == sorted(stop['day_number'] for stop in stops)
