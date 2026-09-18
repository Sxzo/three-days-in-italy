"""The Flask app: serves the planner page and builds itineraries on request.

Every endpoint that takes filters answers in one of two formats. `format=html`
returns a fragment for htmx to swap into the page; anything else returns JSON.
The fragment and the JSON are built from the same get_itinerary() result, so the
two never drift apart.

Filters travel entirely in the query string and planning is deterministic, so a
query string is the whole trip: `/` accepts the same arguments `/itinerary`
does, which is what makes the address bar a shareable itinerary.
"""

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, jsonify, make_response, render_template, request, url_for
from filtering import FILTERS
from itinerary import get_itinerary

PUBLIC_DIR = Path(__file__).resolve().parent.parent / 'public'

# Vercel's CDN serves public/ before a request reaches the function; pointing
# Flask at the same directory keeps the local server serving these files too.
# static_url_path='' means url_for('static', filename='styles.css') -> /styles.css.
app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path='')

DATA_PATH = Path(__file__).resolve().parent.parent / 'data' / 'places.build.json'


def load_places(path=DATA_PATH):
    """Read the built dataset. Returns {place_id: place}.

    Built by pipeline/build_places.py -- rerun that after editing data/italy.json.
    No parsing, no network, nothing that can fail differently between deploys.
    """
    with path.open(encoding='utf-8') as f:
        places = json.load(f)
    for place in places:
        # JSON object keys are strings; weekdays are ints everywhere else.
        place['open_days'] = {int(day): sched for day, sched in place['open_days'].items()}
    return {place['id']: place for place in places}

# Loaded once at import, before the first request.
PLACES = load_places()


# The only arguments the planner reads, and so the only ones worth replaying.
# `format` is absent deliberately: it is an htmx detail meaning "answer with a
# fragment", and the pages these link to always render in full. `edit` is absent
# for the same reason: it says which of the two views to open on, not which trip.
TRIP_ARGS = ('hub', 'start_day', 'include', 'exclude', 'max_budget')


def trip_filters(args):
    """One request's filters, ready to hand straight back to url_for().

    Every link built from these replays the same arguments, so the map and the
    shareable URL are guaranteed to describe the trip the user is looking at.

    Named arguments only, never the request's own. url_for() reads keys that
    start with an underscore (_anchor, _external, _method, _scheme) as its own
    options, so forwarding whatever arrived would let a query string steer URL
    building rather than just describe a trip.
    """
    # getlist keeps every repeated include/exclude value; args.get would forward
    # only the first chip of each group.
    return {name: args.getlist(name) for name in TRIP_ARGS if name in args}


@app.get('/')
def index():
    # Hubs are offered busiest-first: a city with more places can fill three days.
    city_counts = Counter(place['city'] for place in PLACES.values())
    cities = sorted(city_counts.items(), key=lambda city: (-city[1], city[0]))
    today = date.today().isoformat()
    # Tomorrow is the default rather than today: a trip planned now is one the
    # user still has to travel to. Today stays selectable, it is just not assumed.
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # What the form should show. A plain visit leaves all of this empty; a shared
    # link fills it in, so the recipient can tweak the trip rather than restart it.
    selections = {
        'hub': request.args.get('hub', ''),
        'start_day': request.args.get('start_day', '') or tomorrow,
        'include': request.args.getlist('include'),
        'exclude': request.args.getlist('exclude'),
        'max_budget': request.args.get('max_budget', ''),
    }

    # The same two arguments get_itinerary() insists on. With both present the
    # trip is built here and rendered into the page, so a shared link lands on
    # the itinerary itself instead of on a form the visitor has to resubmit.
    #
    # `edit` overrides that. "Edit trip" puts the filters back on screen without
    # navigating, and writes that flag into the address bar so a reload from
    # there opens the form too, rather than rebuilding the trip the user just
    # stepped back out of. The filters stay in the URL either way, so the form
    # still comes back filled in.
    itinerary = None
    map_url = None
    error = None
    if selections['hub'] and request.args.get('start_day') and not request.args.get('edit'):
        try:
            itinerary = get_itinerary(PLACES, request.args)
            map_url = url_for('itinerary_map', **trip_filters(request.args))
        except ValueError as value_error:
            error = str(value_error)

    return render_template(
        'index.html',
        cities=cities,
        filters=FILTERS,
        places=list(PLACES.values()),
        today=today,
        # A link shared after its own start date has passed still has to prefill
        # that date, so the picker's floor is whichever day comes first. ISO
        # dates sort the same as the dates they spell, so a string min is safe.
        min_day=min(today, selections['start_day']),
        selections=selections,
        itinerary=itinerary,
        map_url=map_url,
        error=error,
    )

@app.get('/places')
def list_places():
    return jsonify(list(PLACES.values()))

@app.get('/itinerary')
def itinerary():
    try:
        result = get_itinerary(PLACES, request.args)
        if request.args.get('format') == 'html':
            filters = trip_filters(request.args)
            response = make_response(
                render_template(
                    '_itinerary.html',
                    itinerary=result,
                    map_url=url_for('itinerary_map', **filters),
                )
            )
            # Point the address bar at the page that rebuilds this trip, so what
            # the user is looking at is always what "Copy link" copies. htmx
            # applies this header after the swap, and it beats rebuilding the
            # URL in the browser: these are the arguments that produced the
            # itinerary, not a re-reading of the form that requested it.
            #
            # Replace rather than push, because nothing on that page re-renders
            # in response to popstate: an extra history entry would move the URL
            # back without moving the page with it.
            response.headers['HX-Replace-Url'] = url_for('index', **filters)
            return response
        return jsonify(result)
    except ValueError as error:
        # get_itinerary raises ValueError only for unusable input, never for an
        # empty result, so everything caught here is a 400-class problem.
        if request.args.get('format') == 'html':
            # Deliberately 200: htmx does not swap non-2xx responses, so a 400
            # here would leave the user staring at an unchanged page.
            return render_template('_itinerary_error.html', message=str(error))
        return jsonify({'error': str(error)}), 400


@app.get('/itinerary/map')
def itinerary_map():
    try:
        result = get_itinerary(PLACES, request.args)
    except ValueError as error:
        return render_template('_itinerary_error.html', message=str(error)), 400

    # Flatten the three days into one list numbered 1..N across the whole trip.
    # The sidebar and the map pins both read this numbering, so a stop's badge
    # means the same thing in either place.
    stops = []
    for day_number, day in enumerate(result['days'], start=1):
        for place in day['places']:
            stops.append(
                {
                    **place,
                    'number': len(stops) + 1,
                    'day_number': day_number,
                    'display_date': day['display_date'],
                }
            )

    return render_template(
        'map.html',
        itinerary=result,
        stops=stops,
    )

if __name__ == '__main__':
    # Flask's reloader launches a second Python process. On Windows that child
    # can outlive its terminal after a failed reload, leaving an old server on
    # port 5000. Keep debug error pages, but restart this process manually after
    # code changes so there is only one server process to stop.
    app.run(debug=True, use_reloader=False, port=5000)
