"""Wipe all progress and reseed from the checked-in seed data. Equivalent of
the lustereczko srs_init_db(reset=True) tool. Defaults to the local sqlite
db; set DATABASE_URL to target Postgres instead.

Usage: uv run python scripts/reset_db.py
"""

import os
import sys
from pathlib import Path

# Plain `python scripts/reset_db.py` puts scripts/ on sys.path, not the
# project root (unlike `python -m uvicorn ...`, which start_local.sh uses
# and which puts the cwd on sys.path instead) -- so `app` isn't importable
# without this. Must run before the app.* import below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

from app.implementation.stores.srs_store import SrsStore

load_dotenv()

SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "implementation", "seed_data")
SEED_PATH = os.path.join(SEED_DIR, "seed.json")
PHOTO_MANIFEST_PATH = os.path.join(SEED_DIR, "photo_manifest.json")


def reset():
    """Wipes everyone's progress and every user, plus global species/photo/
    confusion data, then reseeds the global data. Per-user progress rows
    aren't reseeded here -- they're recreated lazily the next time each
    user's cookie hits ensure_user()."""
    store = SrsStore()
    with store.engine.begin() as conn:
        for table in (
            "lesson_items", "lessons", "rank_history", "progress",
            "confusion", "photos", "species", "users",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
    result = store.seed_if_empty(SEED_PATH, PHOTO_MANIFEST_PATH)
    print(f"Reset + reseed result: {result}")


if __name__ == "__main__":
    confirm = input(
        "This will WIPE all lesson progress and reseed from scratch. Type 'yes' to continue: "
    )
    if confirm.strip().lower() == "yes":
        reset()
    else:
        print("Aborted.")
