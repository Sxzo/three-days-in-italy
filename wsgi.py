"""Vercel's entrypoint: the WSGI app it serves.

server/ imports its siblings flatly (`from filtering import ...`), the same
assumption tests/conftest.py makes, so server/ goes on sys.path before the app
is imported.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'server'))

from main import app  # noqa: E402
