"""
Shared pytest fixtures for the fish-recall test suite.

The one rule every fixture here exists to enforce: tests must never touch
data/srs.db (the pre-multi-user file, kept around for a future progress
migration) or data/srs2.db (the real app's current multi-user database).
Every test gets its own throwaway SQLite file under pytest's tmp_path, seeded
fresh from the checked-in seed_data/ (the same fish/photos/confusion data
the real app bootstraps from) -- so tests see realistic data, but each test
run starts from a clean slate and disappears when pytest tears tmp_path down.

Identity: the real app assigns a user_id via cookie on first request (see
api.py's get_user_id). Tests need a *known* user_id before making any
request -- both so direct-DB "rig this precondition" helpers can target the
right progress row, and so multi-user isolation tests can stand up two
distinct identities on purpose. `user_id`/`client` pre-seed the TestClient's
cookie jar with a user_id we already called store.ensure_user() on, instead
of letting the first request assign a random one.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.implementation.api import COOKIE_NAME, create_api
from app.implementation.stores.srs_store import SrsStore

SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "implementation", "seed_data")
SEED_PATH = os.path.join(SEED_DIR, "seed.json")
PHOTO_MANIFEST_PATH = os.path.join(SEED_DIR, "photo_manifest.json")


@pytest.fixture
def store(tmp_path):
    """A fresh SrsStore backed by a temp SQLite file, seeded with the real
    fish/confusion/photo data. Never resolves to data/srs.db -- we always
    pass an explicit database_url, which resolve_database_url() uses as-is."""
    db_path = tmp_path / "test_srs.db"
    s = SrsStore(database_url=f"sqlite:///{db_path}")
    s.seed_if_empty(SEED_PATH, PHOTO_MANIFEST_PATH)
    return s


def _make_client(store, user_id):
    store.ensure_user(user_id)
    api = create_api(store)
    c = TestClient(api)
    c.cookies.set(COOKIE_NAME, user_id)
    return c


@pytest.fixture
def user_id(store):
    """A known user_id, already ensure_user()'d (so its progress rows
    exist) before any HTTP request happens."""
    return "test-user-" + uuid.uuid4().hex[:8]


@pytest.fixture
def client(store, user_id):
    """A TestClient wrapping a fresh FastAPI app instance bound to `store`,
    with its cookie jar pre-seeded so every request in a test acts as the
    same known `user_id` -- this is the "instantiate the FastAPI app with a
    temporary database" path. create_api(store) builds its own SrsEngine
    closed over `store`, entirely independent of the module-level `api` app
    (which stays bound to the real local/production database)."""
    return _make_client(store, user_id)


@pytest.fixture
def second_user_id(store):
    """A second, distinct user_id -- for tests proving one user's activity
    doesn't leak into another's."""
    return "test-user-" + uuid.uuid4().hex[:8]


@pytest.fixture
def second_client(store, second_user_id):
    """A second TestClient against the *same* store, acting as a different
    user than `client`. Used by isolation tests."""
    return _make_client(store, second_user_id)
