# tests/conftest.py
"""
Shared fixtures for the backend test suite.

The environment is set up BEFORE any `backend` import, and that ordering is
load-bearing:

  - `backend/config.py` raises at import time when DATABASE_URL or
    DB_PASSWORD is missing, so they must exist before `Config` is built.
  - importing `backend.services.analytics_service` constructs a module-level
    AnalyticsService(), which performs a real InfluxDB health call. Pointing
    INFLUX_URL at a closed port makes that call fail in milliseconds instead
    of waiting out the client's 10 second default.

`load_dotenv()` does not override variables already present in the
environment, so these assignments win over a real `.env` and keep the suite
hermetic.
"""
import os

os.environ['DATABASE_URL'] = 'postgresql+psycopg2://test:test@127.0.0.1:1/testdb'
os.environ['DB_PASSWORD'] = 'test'
os.environ['INFLUX_URL'] = 'http://127.0.0.1:1'

import pytest

from backend.app import create_app


@pytest.fixture
def app():
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
