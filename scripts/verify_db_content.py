"""Quick sanity check of whatever database DATABASE_URL (or the local sqlite
fallback) points at. Mirrors barobeaver's scripts/verify_db_content.py.

Usage: uv run python scripts/verify_db_content.py
"""

import sys
from pathlib import Path

# Plain `python scripts/verify_db_content.py` puts scripts/ on sys.path, not
# the project root -- see the matching note in reset_db.py. Must run before
# the app.* import below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

from app.implementation.stores.srs_store import SrsStore

load_dotenv()


def verify_data():
    store = SrsStore()
    with store.engine.connect() as conn:
        print(f"Connected to: {store.engine.dialect.name} ({store.database_url})")

        print("\n--- species (first 5, by name) ---")
        rows = conn.execute(text("SELECT id, name FROM species ORDER BY name LIMIT 5")).all()
        for r in rows:
            print(r)
        total = conn.execute(text("SELECT COUNT(*) FROM species")).scalar()
        print(f"species total: {total}")

        photos = conn.execute(text("SELECT COUNT(*) FROM photos")).scalar()
        print(f"photos rows: {photos}")

        users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        print(f"\nusers: {users}")

        print("--- progress by user (level > 0 counts) ---")
        rows = conn.execute(
            text(
                "SELECT u.id, u.email, u.lessons_completed, "
                "COUNT(*) FILTER (WHERE p.level > 0) AS leveled "
                "FROM users u LEFT JOIN progress p ON p.user_id = u.id "
                "GROUP BY u.id, u.email, u.lessons_completed"
            )
            if store.engine.dialect.name == "postgresql"
            else text(
                "SELECT u.id, u.email, u.lessons_completed, "
                "SUM(CASE WHEN p.level > 0 THEN 1 ELSE 0 END) AS leveled "
                "FROM users u LEFT JOIN progress p ON p.user_id = u.id "
                "GROUP BY u.id, u.email, u.lessons_completed"
            )
        ).all()
        for r in rows:
            print(r)

        lessons = conn.execute(text("SELECT COUNT(*) FROM lessons")).scalar()
        print(f"\nlessons rows: {lessons}")


if __name__ == "__main__":
    verify_data()
