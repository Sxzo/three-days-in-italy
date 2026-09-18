"""Fixtures for the test suite.

pipeline/ and server/ import their siblings flatly (`from filtering import ...`),
so both directories go on sys.path before anything is imported. Run the suite
from the repo root with `python -m pytest`.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'

for directory in (ROOT / 'pipeline', ROOT / 'server'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


@pytest.fixture(scope='session')
def source_places():
    """data/italy.json as provided. The source of truth, never edited."""
    return json.loads((DATA / 'italy.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='session')
def built_places():
    """data/places.build.json, the artifact server/main.py loads and serves."""
    return json.loads((DATA / 'places.build.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='session')
def places():
    """The same artifact as the app sees it: {place_id: place}, weekdays as ints."""
    from main import load_places

    return load_places()


@pytest.fixture()
def client():
    """A Flask test client. No server to start, no port to pick."""
    from main import app

    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def make_place():
    """A synthetic place, so filtering and planner tests don't ride on real rows.

    An unremarkable Rome museum: open 09:00-18:00 every day, every week, all
    year. Pass overrides for whatever the test is actually about.
    """

    def _make_place(**overrides):
        return {
            'id': 'test_place',
            'name': 'Test Place',
            'type': 'museum',
            'city': 'Rome',
            'latitude': 41.8902,
            'longitude': 12.4922,
            'posted_hours': '9:00-18:00',
            'duration_minutes': 60,
            'price_range': '€',
            'rating': 4.0,
            'tags': [],
            'seasonal_notes': None,
            'booking_required': False,
            'hours_source': 'posted',
            'open_days': {
                day: {
                    'hours': [{'open': '09:00', 'close': '18:00'}],
                    'weeks': [1, 2, 3, 4, 5, -1],
                }
                for day in range(7)
            },
            'open_months': list(range(1, 13)),
            **overrides,
        }

    return _make_place
