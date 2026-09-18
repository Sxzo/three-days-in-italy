"""Did the LLM read the hours field correctly?

Five real rows from italy.json, each picked because it is a different way the
posted hours and the seasonal notes can disagree. Checked against the built
artifact, so this covers the whole path from the source file to what the
planner schedules -- and against the committed answers in
data/hours_llm_cache.json, so it costs nothing and makes no API call.
"""

ALL_WEEKS = [1, 2, 3, 4, 5, -1]
ALL_MONTHS = list(range(1, 13))


def given(source_places, places, place_id, hours, seasonal_notes):
    """Fetch a built row by id, first confirming italy.json still says this.

    The expectations below are written against these exact strings, so if the
    source row is edited the test should be re-read rather than re-run.
    """
    source = next(row for row in source_places if row['id'] == place_id)
    assert (source['hours'], source['seasonal_notes']) == (hours, seasonal_notes), (
        f'{place_id}: italy.json changed; revisit what this test expects'
    )
    place = places[place_id]
    assert place['hours_source'] == 'llm', f'{place_id}: no longer parsed by the LLM'
    return place


def test_the_vatican_confines_its_sunday_exception_to_sunday(source_places, places):
    """place_010, Vatican Museums: 'Mon-Sat 9:00-18:00' plus a Sunday exception.

    The hard case for the prompt. "Last Sunday of the month" names a week, so
    Sunday has to open on week -1 only, while the posted Mon-Sat is left
    completely alone -- the exception must not leak onto the other six days.
    """
    vatican = given(
        source_places, places, 'place_010',
        'Mon-Sat 9:00-18:00',
        'Closed Sundays except last Sunday of the month (free entry, massive crowds).',
    )

    assert sorted(vatican['open_days']) == list(range(7))
    assert vatican['open_days'][0]['weeks'] == [-1]
    assert all(vatican['open_days'][day]['weeks'] == ALL_WEEKS for day in range(1, 7))
    # The posted window carries through to every open day, Sunday included.
    assert all(
        schedule['hours'] == [{'open': '09:00', 'close': '18:00'}]
        for schedule in vatican['open_days'].values()
    )
    # Free entry and crowds are advice, so the place stays open all year.
    assert vatican['open_months'] == ALL_MONTHS


def test_villa_del_balbianello_closes_for_the_season_and_keeps_its_odd_days(source_places, places):
    """place_064: 'Tues, Thurs-Sun 10:00-18:00' with a genuine seasonal closure.

    Two things at once -- a comma-separated day list that has to expand
    correctly, and notes that really do shut the place for five months.
    """
    villa = given(
        source_places, places, 'place_064',
        'Tues, Thurs-Sun 10:00-18:00',
        'Open April-October only.',
    )

    # Tuesday, then Thursday through Sunday: Monday and Wednesday stay closed,
    # which the contract expresses by leaving them out of open_days entirely.
    assert sorted(villa['open_days']) == [0, 2, 4, 5, 6]
    assert all(
        schedule['hours'] == [{'open': '10:00', 'close': '18:00'}]
        and schedule['weeks'] == ALL_WEEKS
        for schedule in villa['open_days'].values()
    )
    assert villa['open_months'] == [4, 5, 6, 7, 8, 9, 10]


def test_ceresio_7_stays_open_when_only_its_rooftop_is_seasonal(source_places, places):
    """place_065: 'Wed-Sun 12:30-23:30' with a note about the rooftop only.

    The trap. "Open May-September only" looks exactly like a closure, but it
    describes one part of the bar, so the bar itself keeps all twelve months.
    Reading this as a closure would drop the place from most trips of the year.
    """
    ceresio = given(
        source_places, places, 'place_065',
        'Wed-Sun 12:30-23:30',
        'Rooftop open May-September only.',
    )

    assert ceresio['open_months'] == ALL_MONTHS
    # Wed-Sun is inclusive and wraps past Saturday to pick up Sunday.
    assert sorted(ceresio['open_days']) == [0, 3, 4, 5, 6]
    assert all(
        schedule['hours'] == [{'open': '12:30', 'close': '23:30'}]
        for schedule in ceresio['open_days'].values()
    )


def test_the_parma_tour_takes_its_hours_from_the_notes(source_places, places):
    """place_053: no posted hours at all, but the notes describe when it runs.

    italy.json leaves this row's hours null, so the only schedule available is
    the prose. "Weekday mornings" has to become Monday-Friday, 08:00-12:00.
    """
    tour = given(
        source_places, places, 'place_053',
        None,
        'Tours run weekday mornings only — plan ahead.',
    )

    assert sorted(tour['open_days']) == [1, 2, 3, 4, 5]
    assert all(
        schedule['hours'] == [{'open': '08:00', 'close': '12:00'}]
        for schedule in tour['open_days'].values()
    )
    assert tour['open_months'] == ALL_MONTHS


def test_the_risotto_festival_is_open_at_an_unknown_time_in_one_month(source_places, places):
    """place_090: no posted hours and no time of day anywhere in the notes.

    The empty-hours case. Every day stays open with an empty `hours` list,
    meaning "time of day unknown" -- inventing 09:00-18:00 would have the
    planner promise a window the data never claimed, and dropping the days
    would close a festival that is genuinely open.
    """
    festival = given(
        source_places, places, 'place_090',
        None,
        'October only — check exact festival dates before planning.',
    )

    assert sorted(festival['open_days']) == list(range(7))
    assert all(schedule['hours'] == [] for schedule in festival['open_days'].values())
    # A single named month is still a real closure for the rest of the year.
    assert festival['open_months'] == [10]
