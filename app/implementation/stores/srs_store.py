"""
Storage layer for the fish-recall SRS data.

Mirrors barobeaver's stores/station_store.py pattern: SQLAlchemy Core
(not the ORM) so the exact same table definitions and queries work against
both local SQLite (default, for local dev) and Postgres (on Railway, via
DATABASE_URL). All queries in srs_engine.py use raw `text()` SQL with named
bind params and standard `ON CONFLICT` upsert syntax, which is supported
identically by SQLite (3.24+) and Postgres -- so no dialect branching is
needed anywhere in this project, unlike station_store.py which used
dialect-specific insert() constructs.

Multi-user shape (added after the original single-user prototype): `species`
holds the static, global fish facts (shared by everyone). `progress` holds
one row per (user_id, fish_id) with everything SRS-related -- level,
streaks, due dates, mastery. `users` is intentionally minimal for now: a
cookie-assigned id, plus an email column that stays unused until the
"send me a login link" flow is built. `confusion` and `photos` stay global
(shared species-level data, not per-user) by design.
"""

import json
import os
import time

from sqlalchemy import (
    MetaData,
    Table,
    Column,
    Integer,
    Float,
    String,
    Text,
    create_engine,
    text,
)

# NOTE: Column(..., default=0) is a client-side (Python/Core-insert) default
# -- it is NOT emitted as a DDL DEFAULT clause, so it has no effect on the
# raw text() INSERTs used throughout this file and srs_engine.py (which only
# ever list the columns they care about). We use server_default instead so
# every column that should start at zero/empty actually does, regardless of
# which columns a given INSERT statement lists explicitly. This matters a
# lot for the multi-user shape below: ensure_user() backfills a `progress`
# row per species by inserting only (user_id, fish_id) and relying on
# server_default to fill in the rest.


LOCAL_DB_FILENAME = "srs2.db"
# The multi-user schema (users/species/progress, plus user_id columns on
# lessons/rank_history) lives in a new file, srs2.db, instead of reusing
# the original srs.db. metadata.create_all() only creates tables that don't
# already exist -- it can't add a column to a `lessons`/`rank_history` table
# that predates this refactor, so continuing to point at the old file would
# crash on every request (learned the hard way: see planning/ for the
# traceback). Renaming instead of deleting also means the pre-multi-user
# data/srs.db is preserved untouched, so a real progress migration can
# still read from it later once there's a way to know which user_id it
# should become.


def resolve_database_url(database_url=None):
    """Rewrite postgres://... to postgresql+psycopg://... for SQLAlchemy + psycopg3,
    same fix as barobeaver's nws.py. Falls back to a local sqlite file under data/
    when no DATABASE_URL is provided (or the env var is unset)."""
    db_url = database_url if database_url is not None else os.environ.get("DATABASE_URL")

    if not db_url:
        db_dir = os.environ.get("FISH_DATA_DIR", "data")
        os.makedirs(db_dir, exist_ok=True)
        return f"sqlite:///{os.path.join(db_dir, LOCAL_DB_FILENAME)}"

    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql+psycopg://", 1)
    return db_url


class SrsStore:
    def __init__(self, database_url=None):
        self.database_url = resolve_database_url(database_url)
        self.engine = create_engine(self.database_url)
        print(f"SrsStore connected to: {self.engine.dialect.name}")
        self.metadata = MetaData()

        self.users = Table(
            "users",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("email", String, nullable=True),
            Column("created_at", Float, server_default=text("0")),
            Column("last_login_at", Float, server_default=text("0")),
            Column("lessons_completed", Integer, server_default=text("0")),
        )

        self.species = Table(
            "species",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("name", String),
            Column("scientific_name", String),
            Column("size", String),
            Column("features", Text),
            Column("photo_file", String),
            Column("mnemonic", Text),
        )

        self.progress = Table(
            "progress",
            self.metadata,
            Column("user_id", String, primary_key=True),
            Column("fish_id", String, primary_key=True),
            Column("level", Integer, server_default=text("0")),
            Column("streak_success", Integer, server_default=text("0")),
            Column("streak_fail", Integer, server_default=text("0")),
            Column("seen_count", Integer, server_default=text("0")),
            Column("correct_count", Integer, server_default=text("0")),
            Column("wrong_count", Integer, server_default=text("0")),
            Column("last_reviewed_at", Float, server_default=text("0")),
            Column("next_due_at", Float, server_default=text("0")),
            Column("mastered", Integer, server_default=text("0")),
            Column("mastered_at", Float, server_default=text("0")),
            Column("last_result", String, server_default=text("''")),
        )

        self.photos = Table(
            "photos",
            self.metadata,
            Column("fish_id", String, primary_key=True),
            Column("seq", Integer, primary_key=True),
            Column("file", String),
            Column("credit", String),
        )

        self.confusion = Table(
            "confusion",
            self.metadata,
            Column("fish_id", String, primary_key=True),
            Column("other_id", String, primary_key=True),
            Column("weight", Integer, server_default=text("1")),
        )

        self.lessons = Table(
            "lessons",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", String),
            Column("started_at", Float),
            Column("completed_at", Float),
            Column("planned_size", Integer),
            Column("target_rate", Float),
            Column("correct_count", Integer, server_default=text("0")),
            Column("wrong_count", Integer, server_default=text("0")),
            Column("status", String, server_default=text("'active'")),
        )

        self.lesson_items = Table(
            "lesson_items",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("lesson_id", Integer),
            Column("seq", Integer),
            Column("fish_id", String),
            Column("level_at_plan", Integer),
            Column("is_retry", Integer, server_default=text("0")),
            Column("is_reinforce", Integer, server_default=text("0")),
            Column("status", String, server_default=text("'pending'")),
            Column("correct", Integer),
        )

        self.rank_history = Table(
            "rank_history",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", String),
            Column("ts", Float),
            Column("score", Float),
            Column("mastered_count", Integer),
            Column("lessons_completed", Integer),
            Column("reason", String),
        )

        self.metadata.create_all(self.engine)

    def ensure_user(self, user_id, email=None):
        """Idempotent: create the user (and backfill a progress row per
        species) on first sight, otherwise just bump last_login_at. Called
        on every request via the get_user_id dependency in api.py, so this
        needs to be cheap on the "already exists" path.

        Returns True if this was a brand-new user, False otherwise.
        """
        now = time.time()
        with self.engine.begin() as conn:
            inserted = conn.execute(
                text(
                    "INSERT INTO users (id, email, created_at, last_login_at, lessons_completed) "
                    "VALUES (:id, :email, :now, :now, 0) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": user_id, "email": email, "now": now},
            ).rowcount

            if not inserted:
                conn.execute(
                    text("UPDATE users SET last_login_at=:now WHERE id=:id"),
                    {"now": now, "id": user_id},
                )
                return False

            # Brand-new user: give them a zeroed progress row for every
            # species (server_default fills in level=0, next_due_at=0,
            # etc.) and an initial rank_history point, mirroring what
            # seed_if_empty used to do once, globally, for the old
            # single-user `fish` table.
            #
            # SQLite's upsert clause only follows INSERT...VALUES, not
            # INSERT...SELECT (confirmed empirically -- "near DO: syntax
            # error" on INSERT INTO progress SELECT ... ON CONFLICT ...),
            # so we fetch the species ids in Python and loop VALUES-based
            # upserts, matching the pattern already used in seed_if_empty.
            # This only runs once per new user, so the extra round trips
            # don't matter.
            species_ids = conn.execute(text("SELECT id FROM species")).scalars().all()
            for fid in species_ids:
                conn.execute(
                    text(
                        "INSERT INTO progress (user_id, fish_id) VALUES (:uid, :fid) "
                        "ON CONFLICT (user_id, fish_id) DO NOTHING"
                    ),
                    {"uid": user_id, "fid": fid},
                )
            conn.execute(
                text(
                    "INSERT INTO rank_history (user_id, ts, score, mastered_count, lessons_completed, reason) "
                    "VALUES (:uid, :now, 0, 0, 0, 'init')"
                ),
                {"uid": user_id, "now": now},
            )
            return True

    def seed_if_empty(self, seed_path, photo_manifest_path):
        """Populate species/confusion/photos from the checked-in seed data,
        but only the first time (i.e. when the species table is empty) --
        mirrors srs_init_db's `fresh = not os.path.exists(DB_PATH)` check,
        but keyed off row count instead of file existence so it works the
        same way against Postgres. This only seeds global data -- per-user
        `progress` rows are created lazily by ensure_user()."""
        with self.engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM species")).scalar()
            if count:
                return {"seeded": False, "fish_count": count}

            with open(seed_path) as f:
                seed = json.load(f)
            for fish in seed["fish"]:
                conn.execute(
                    text(
                        "INSERT INTO species (id, name, scientific_name, size, features, photo_file, mnemonic) "
                        "VALUES (:id, :name, :scientific_name, :size, :features, :photo_file, :mnemonic) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    fish,
                )
            for a, b in seed["confusion_pairs"]:
                conn.execute(
                    text(
                        "INSERT INTO confusion (fish_id, other_id, weight) VALUES (:a, :b, 2) "
                        "ON CONFLICT (fish_id, other_id) DO NOTHING"
                    ),
                    {"a": a, "b": b},
                )
                conn.execute(
                    text(
                        "INSERT INTO confusion (fish_id, other_id, weight) VALUES (:a, :b, 2) "
                        "ON CONFLICT (fish_id, other_id) DO NOTHING"
                    ),
                    {"a": b, "b": a},
                )

            with open(photo_manifest_path) as f:
                manifest = json.load(f)
            n_photos = 0
            for entry in manifest:
                fid = entry["id"]
                for i, p in enumerate(entry["photos"], start=1):
                    conn.execute(
                        text(
                            "INSERT INTO photos (fish_id, seq, file, credit) "
                            "VALUES (:fish_id, :seq, :file, :credit) "
                            "ON CONFLICT (fish_id, seq) DO UPDATE SET file=:file, credit=:credit"
                        ),
                        {"fish_id": fid, "seq": i, "file": p["web_file"], "credit": p.get("credit", "")},
                    )
                    n_photos += 1

            return {"seeded": True, "fish_count": len(seed["fish"]), "photo_count": n_photos}
