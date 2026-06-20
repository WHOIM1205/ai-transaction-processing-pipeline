"""Shared pytest fixtures.

The API tests exercise the HTTP layer without a real database or broker: the DB
dependency is overridden and the repository/service calls are monkeypatched per
test. This keeps the suite fast and runnable with a plain `pytest` (no Docker).
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app


@pytest.fixture
def client():
    # Override the DB dependency with a no-op; tests stub the repo/service calls
    # that would otherwise use the session.
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
