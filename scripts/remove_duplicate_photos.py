"""Remove `photos` rows that are no longer in photo_manifest.json (currently
the 28 duplicate photos found and stripped from the manifest: species seeded
with 3 photos where seq 3 was byte-identical to an earlier one). Unlike
add_missing_species.py, this only ever deletes -- never touches `progress`
or any other per-user data.

Safe to run repeatedly: only deletes rows whose (fish_id, seq) isn't in the
current manifest, so a second run is a no-op.

Usage: uv run python scripts/remove_duplicate_photos.py
       (set DATABASE_URL first, or use `railway run` when targeting
       staging/production -- see add_missing_species.py for the
       DATABASE_PUBLIC_URL pattern needed there)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

from app.implementation.stores.srs_store import SrsStore

load_dotenv()

PHOTO_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "app" / "implementation" / "seed_data" / "photo_manifest.json"


def remove_duplicate_photos():
    store = SrsStore()
    print(f"Connected to: {store.engine.dialect.name}")

    with open(PHOTO_MANIFEST_PATH) as f:
        manifest = json.load(f)

    valid_pairs = {(entry["id"], i) for entry in manifest for i in range(1, len(entry["photos"]) + 1)}

    with store.engine.begin() as conn:
        rows = conn.execute(text("SELECT fish_id, seq, file FROM photos")).all()
        removed = []
        for fish_id, seq, file in rows:
            if (fish_id, seq) not in valid_pairs:
                conn.execute(
                    text("DELETE FROM photos WHERE fish_id=:fid AND seq=:seq"),
                    {"fid": fish_id, "seq": seq},
                )
                removed.append((fish_id, seq, file))

    print(f"Removed {len(removed)} photo rows no longer in the manifest:")
    for fish_id, seq, file in removed:
        print(f"  {fish_id} seq={seq} ({file})")


if __name__ == "__main__":
    remove_duplicate_photos()
