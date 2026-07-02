"""
FastAPI JSON API for the fish-recall SRS engine.

This is the FastAPI half of the "backend tools migrated to FastAPI"
requirement -- it's the direct replacement for the lustereczko custom tools
(srs_init_db, srs_lesson). It's mounted under /api by the FastHTML app in
main.py, so the two frameworks each do the job they're best at: FastAPI for
JSON endpoints, FastHTML for serving the page shell + static assets.

Identity: low-friction, cookie-based. A visitor with no cookie gets a random
uuid4 assigned and set as an httponly cookie on their first response; every
route depends on get_user_id() to read (or assign) that cookie and scope all
engine calls to it. No password, no signing -- copying someone's cookie
value would let you act as them. That's an accepted tradeoff for a personal
trainer app right now (see planning/ for the discussion); `users.email` and
`last_login_at` already exist in the schema so a "send me a login link"
flow can be layered on later without another migration.
"""

import os
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from app.implementation.srs_engine import SrsEngine
from app.implementation.stores.srs_store import SrsStore

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed_data")
SEED_PATH = os.path.join(SEED_DIR, "seed.json")
PHOTO_MANIFEST_PATH = os.path.join(SEED_DIR, "photo_manifest.json")

COOKIE_NAME = "fr_uid"
COOKIE_MAX_AGE = 5 * 365 * 24 * 3600  # ~5 years -- this cookie *is* the account


class SubmitRequest(BaseModel):
    item_id: int
    answer: str | None = None


def create_api(store: SrsStore) -> FastAPI:
    """Build a fresh FastAPI app wired to the given SrsStore.

    Factored out so tests can instantiate the real API against an isolated,
    temporary database instead of the module-level `api` app below (which is
    bound to the real local/production store) -- see app/tests/conftest.py.
    Every route closes over its own SrsEngine(store), so two apps built from
    two different stores never share state.
    """
    engine = SrsEngine(store)
    app = FastAPI(title="Caribbean Fish Recall API")

    def get_user_id(request: Request, response: Response) -> str:
        """Read the identity cookie, assigning a new one on first visit.
        Also guarantees a `users` row (and a zeroed `progress` row per
        species) exists before any engine call runs."""
        uid = request.cookies.get(COOKIE_NAME)
        if not uid:
            uid = uuid.uuid4().hex
            response.set_cookie(
                COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax"
            )
        store.ensure_user(uid)
        return uid

    @app.post("/lesson/start")
    def lesson_start(user_id: str = Depends(get_user_id)):
        return engine.start(user_id)

    @app.get("/lesson/next_item")
    def lesson_next_item(lesson_id: int, user_id: str = Depends(get_user_id)):
        result = engine.next_item(lesson_id, user_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "lesson not found"))
        return result

    @app.post("/lesson/submit")
    def lesson_submit(body: SubmitRequest, user_id: str = Depends(get_user_id)):
        result = engine.submit(body.item_id, body.answer, user_id)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "submit failed"))
        return result

    @app.get("/stats")
    def stats(user_id: str = Depends(get_user_id)):
        return engine.stats(user_id)

    @app.get("/browse")
    def browse(user_id: str = Depends(get_user_id)):
        return engine.browse(user_id)

    return app


def _build_default_store() -> SrsStore:
    default_store = SrsStore()
    seed_result = default_store.seed_if_empty(SEED_PATH, PHOTO_MANIFEST_PATH)
    print(f"SrsStore seed check: {seed_result}")
    return default_store


# Module-level singleton: the real app (main.py) imports `api` directly, so
# this still resolves to DATABASE_URL / data/srs2.db exactly as before
# (srs2.db, not the original srs.db -- see LOCAL_DB_FILENAME in srs_store.py).
# Tests should use create_api(store) with a temp store instead of importing
# this object.
store = _build_default_store()
engine = SrsEngine(store)
api = create_api(store)
