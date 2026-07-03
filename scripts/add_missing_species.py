"""Add any species/confusion/photo rows from seed_data/ that aren't already
in the database, without touching existing data -- unlike reset_db.py, this
never wipes anything. Also backfills a zeroed `progress` row for every
existing user for each newly-added species, mirroring what ensure_user()
does for brand-new users (existing users never get progress rows for
species that didn't exist yet when they were first seen).

Safe to run repeatedly -- every insert is ON CONFLICT DO NOTHING (or DO
UPDATE for photos, matching seed_if_empty's own convention), so species
already present are left untouched.

Usage: uv run python scripts/add_missing_species.py
       (set DATABASE_URL first, or use `railway run` when targeting
       staging/production -- see DATABASE_URL is unset -> local sqlite)
"""

import json
import sys
from pathlib import Path

# Plain `python scripts/add_missing_species.py` puts scripts/ on sys.path,
# not the project root -- see the matching note in reset_db.py. Must run
# before the app.* import below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

from app.implementation.stores.srs_store import SrsStore

load_dotenv()

SEED_DIR = Path(__file__).resolve().parent.parent / "app" / "implementation" / "seed_data"
SEED_PATH = SEED_DIR / "seed.json"
PHOTO_MANIFEST_PATH = SEED_DIR / "photo_manifest.json"


def add_missing_species():
    store = SrsStore()
    print(f"Connected to: {store.engine.dialect.name}")

    with open(SEED_PATH) as f:
        seed = json.load(f)
    with open(PHOTO_MANIFEST_PATH) as f:
        manifest = json.load(f)

    with store.engine.begin() as conn:
        existing_ids = set(conn.execute(text("SELECT id FROM species")).scalars().all())

        added_species = []
        for fish in seed["fish"]:
            result = conn.execute(
                text(
                    "INSERT INTO species (id, name, scientific_name, size, features, photo_file, mnemonic) "
                    "VALUES (:id, :name, :scientific_name, :size, :features, :photo_file, :mnemonic) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                fish,
            )
            if fish["id"] not in existing_ids and result.rowcount:
                added_species.append(fish["id"])

        added_confusion = 0
        for a, b in seed["confusion_pairs"]:
            for x, y in ((a, b), (b, a)):
                result = conn.execute(
                    text(
                        "INSERT INTO confusion (fish_id, other_id, weight) VALUES (:a, :b, 2) "
                        "ON CONFLICT (fish_id, other_id) DO NOTHING"
                    ),
                    {"a": x, "b": y},
                )
                added_confusion += result.rowcount

        added_photos = 0
        for entry in manifest:
            fid = entry["id"]
            for i, p in enumerate(entry["photos"], start=1):
                result = conn.execute(
                    text(
                        "INSERT INTO photos (fish_id, seq, file, credit) "
                        "VALUES (:fish_id, :seq, :file, :credit) "
                        "ON CONFLICT (fish_id, seq) DO UPDATE SET file=:file, credit=:credit"
                    ),
                    {"fish_id": fid, "seq": i, "file": p["web_file"], "credit": p.get("credit", "")},
                )
                if fid in added_species:
                    added_photos += result.rowcount

        # Backfill a zeroed progress row for every existing user, for just
        # the newly-added species -- existing users never got a row for
        # these since ensure_user() only backfills at first-sight time.
        user_ids = conn.execute(text("SELECT id FROM users")).scalars().all()
        backfilled_progress = 0
        for uid in user_ids:
            for fid in added_species:
                result = conn.execute(
                    text(
                        "INSERT INTO progress (user_id, fish_id) VALUES (:uid, :fid) "
                        "ON CONFLICT (user_id, fish_id) DO NOTHING"
                    ),
                    {"uid": uid, "fid": fid},
                )
                backfilled_progress += result.rowcount

    print(f"Species added: {added_species or 'none'}")
    print(f"Confusion pairs added: {added_confusion}")
    print(f"Photo rows added (for new species): {added_photos}")
    print(f"Existing users backfilled: {len(user_ids)} users x {len(added_species)} new species "
          f"= {backfilled_progress} progress rows created")


if __name__ == "__main__":
    add_missing_species()
