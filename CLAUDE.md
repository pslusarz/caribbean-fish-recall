# Caribbean Fish Recall

A spaced-repetition trainer for identifying 58 Caribbean reef fish species. Originally
built as a lustereczko/Cowork prototype (Python "custom tools" + a single HTML/JS blob,
SQLite only, photos as base64 data URIs); migrated to a standalone FastAPI + FastHTML app
intended for Railway deployment. See `planning/LUSTERECZKO-TO-RAILWAY-MIGRATION-GUIDE.md`
for the generalized version of that migration process, and
`planning/CURRENT-EPIC-LUSTERECZKO-MIGRATION-IN-PROGRESS.md` for the full history of how
this codebase got here (useful if something looks like it was done a certain way for a
non-obvious reason).

## Architecture

Two frameworks, each doing the job it's best at:

- **FastAPI** (`app/implementation/api.py`) serves the JSON API — the direct replacement
  for the old lustereczko custom tools (`srs_init_db`, `srs_lesson`).
- **FastHTML** (`app/implementation/main.py`) serves the page shell (`app/implementation/components/index_page.py`,
  a single-page vanilla-JS app embedded as a string) and static assets (`public/photos/*.jpg`
  via `static_path="public"`).

FastHTML's app is Starlette-based, same as FastAPI, so the API is mounted directly:
`app.mount("/api", api)`. The frontend calls `/api/*` via plain `fetch()` (see `fetchApi()`
in `index_page.py`) — no separate build step, no JS framework.

**Storage** (`app/implementation/stores/srs_store.py`): SQLAlchemy Core, not the ORM — every
query in `srs_engine.py` is raw `text()` SQL with named params and standard `ON CONFLICT`
upsert syntax, which SQLite (3.24+) and Postgres both support identically, so there's no
dialect branching anywhere except the two spots that genuinely need it (`RETURNING id` on
insert, and `postgres://` → `postgresql+psycopg://` URL rewriting). Local dev defaults to
SQLite; set `DATABASE_URL` (Railway provides this via the Postgres plugin) to target Postgres.

**Engine** (`app/implementation/srs_engine.py`): all business logic — Levenshtein spelling
grading, confusion-weighted multiple-choice distractors, spaced-repetition scheduling,
lesson composition, promotion/demotion/mastery. Pure functions of `(store, user_id, ...)` in
→ dict out; no FastAPI/HTTP concerns leak in here.

## Schema

Seven tables. The key design split: **`species` is global, `progress` is per-user.**

- `species` — static fish facts shared by everyone (name, scientific_name, size, features,
  photo_file, mnemonic). Seeded once from `app/implementation/seed_data/seed.json`.
- `progress` — everything SRS-related (level, streaks, seen/correct/wrong counts,
  next_due_at, mastered), composite PK `(user_id, fish_id)`. A user gets a zeroed row per
  species the moment they're first seen (see `ensure_user()` below) — don't assume a
  `progress` row needs to be created lazily elsewhere; every route can assume it already
  exists for any `(user_id, fish_id)` pair.
- `users` — `id` (the cookie value, a plain uuid4 hex string), `email` (nullable, unused —
  reserved for a future "send me a login link" flow), `created_at`, `last_login_at`,
  `lessons_completed`.
- `lessons` / `lesson_items` — one row per lesson attempt / per question within it.
  `lessons.user_id` scopes ownership.
- `rank_history` — score snapshots over time, also `user_id`-scoped.
- `confusion`, `photos` — **intentionally stay global**, not per-user. `confusion` is a
  seeded "these species look alike" signal (used to pick MC distractors) that also gets
  incremented whenever *any* user answers wrong and matches another fish — meaning one
  user's mistakes currently nudge distractor selection for everyone. That's a known,
  deliberate simplification, not an oversight — flagged as worth revisiting if it ever
  matters, but nobody has asked for per-user confusion tracking.

There is also a pre-multi-user `data/srs.db` (SQLite only, not part of the schema above) —
see the "Legacy single-user database" section in the handoff doc under `planning/` for
what that is and why it's still around.

## Identity model

No accounts, no passwords. `api.py`'s `get_user_id(request, response)` dependency reads a
`fr_uid` cookie; if absent, assigns a random `uuid4().hex`, sets it as an httponly,
`samesite=lax` cookie (~5 year max-age), and calls `store.ensure_user(uid)`. Every route
depends on this. `ensure_user()` is idempotent: on an existing user it just bumps
`last_login_at`; on a brand-new user it also backfills a zeroed `progress` row per species
and logs an initial `rank_history` point.

This is a deliberate low-friction tradeoff: the cookie *is* the account, nothing is signed,
copying someone's cookie value lets you act as them. Acceptable for a personal trainer app
right now. `users.email`/`last_login_at` exist so a real "send yourself a login link, tie it
to this uuid" flow can be added later without another schema migration.

**Ownership checks matter.** `lesson_id` and `item_id` are plain incrementing integers.
`next_item()` and `submit()` both verify the requested lesson/item actually belongs to the
calling `user_id` (via a JOIN against `lessons`) before doing anything with it — never trust
a caller-supplied id alone. This was a real bug caught by the isolation tests (see below):
`start()`'s "abandon my other active lesson" cleanup was originally unscoped and would have
abandoned *every* user's active lesson every time anyone started a new one.

## SRS mechanics (the part that's easy to misremember)

Level ladder 0-4 per fish, per user. `X = 2` (in `srs_engine.py`) is the promotion/demotion
threshold: it takes **two consecutive correct answers** to climb a level, not one, and two
consecutive wrong answers to drop one.

A brand-new fish's first lesson touch is an **intro card** (`level_at_plan=0`, shown once,
doesn't affect level/streak — `is_intro` skips the whole stats-update branch except
`seen_count`). 4-8 items later in the *same* lesson, a **reinforce clone** appears
(`level_at_plan=1`, `is_reinforce=1`) — this is the fish's first real quiz. Answering it
correctly sets `streak_success=1`, which is *below* the promotion threshold, so `level`
stays 0. The second correct answer needed to actually promote has to come from a separate
encounter, scheduled via `next_due_at = now + REVIEW_GAP[1]` (15 minutes) — meaning **a
single lesson, no matter how perfectly answered, cannot move a fish off level 0**, and
therefore can't move the `score`/`mastered` stats either (those are computed from `level`,
not from accuracy). This is expected behavior, confirmed against real data, not a bug — see
the handoff doc for the conversation where this came up. It's also a UX rough edge worth
remembering: a flawless first lesson currently shows "0% rank" with no more encouraging
signal above the fold.

Lesson composition targets `TARGET_RATE = 0.70`: given the blended accuracy of everything
currently due for review, it picks a mix of new-vs-review items sized to land around a 70%
hit rate. `REVIEW_GAP` (short-term, minutes-to-hours, cram-friendly) governs when a card
resurfaces within lesson content; `DECAY_GAP` (day-scale) governs long-term forgetting —
`_decay_pass()` silently drops a fish's level if it hasn't been reviewed within its decay
window, run at the top of both `start()` and `stats()`.

Missed items get a same-lesson "encore" retry batch once the main queue is exhausted
(`is_retry=1`) — these update `lesson_items.status` but deliberately don't touch
`lessons.correct_count`/`wrong_count` or the fish's `progress` row (see the `if not is_retry`
guard throughout `submit()`). Don't "fix" this without checking `test_missed_item_gets_a_same_lesson_retry_that_does_not_affect_lesson_tally`
first — it's intentional, not an oversight.

## Known footguns (learned the hard way this project)

- **`Column(..., default=0)` is a client-side, Core-insert-time default — it is NOT a DDL
  default.** It silently does nothing against this codebase's raw `text()` INSERTs (which
  only ever list the columns they care about). Use `server_default=text("0")` instead for
  any column that should genuinely start at zero/empty regardless of which columns an
  INSERT statement lists. This bit us twice: `fish`/`lessons` columns landing as `NULL`
  instead of `0` (so `NULL + 1` stayed `NULL` forever), and `mastered_at` never actually
  being written by `submit()` despite `mastered_now` being reported correctly.
- **SQLite's `ON CONFLICT` upsert clause only follows `INSERT ... VALUES`, not
  `INSERT ... SELECT`.** `INSERT INTO progress (user_id, fish_id) SELECT :uid, id FROM
  species ON CONFLICT ... DO NOTHING` throws `near "DO": syntax error`. Loop VALUES-based
  upserts instead (see `ensure_user()`) — it's a one-time cost per new user, doesn't matter
  performance-wise.
- **`metadata.create_all()` does not migrate schemas.** It only creates tables that don't
  already exist; it will never `ALTER TABLE` to add a column to a table that predates a
  schema change. This is why the local SQLite filename is `srs2.db`, not `srs.db` — adding
  `user_id` to `lessons`/`rank_history` while a pre-existing `data/srs.db` still had those
  tables (without the column) meant every request crashed with `table lessons has no column
  named user_id`. Renaming instead of deleting/migrating both fixed it immediately and kept
  the old single-user data intact for a possible future migration. **Before this app has real
  Postgres data anyone cares about, replace this create-all-only approach with a real
  migration tool (Alembic is the standard choice) — don't keep solving schema changes by
  renaming the database file once it matters.**
- **Plain `python scripts/foo.py` puts `scripts/` on `sys.path`, not the project root** —
  `from app...` imports fail with `ModuleNotFoundError: No module named 'app'` unless the
  script explicitly does `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
  (see the top of `reset_db.py`/`verify_db_content.py`). `start_local.sh`'s `uvicorn`
  invocation doesn't hit this because `python -m uvicorn ...` puts the *cwd* on `sys.path`,
  which plain script invocation doesn't do. Any new one-off script under `scripts/` needs
  this same bootstrap if it imports from `app`.

## Dev workflow

```bash
uv sync
./scripts/start_local.sh          # port 5002, hot-reload, kills any previous instance
uv run python scripts/reset_db.py # wipes everyone's progress + reseeds species/photos/confusion
uv run python scripts/verify_db_content.py  # quick read-only sanity check of whatever DB is active
uv run pytest app/tests/          # full test suite
```

`start_local.sh` kills leftovers two ways: first by the recorded `data/server.pid` (and any
child processes of it — `uvicorn --reload` forks a child to actually serve, and the pid file
holds the *parent* reloader's pid), then by whatever's still bound to port 5002 as a
catch-all. Logs land in `data/app.log` (rotated to `.old` on each restart).

## Testing conventions

`app/tests/conftest.py` fixtures: `store` (a temp-file SQLite `SrsStore` per test, seeded
from the real `seed_data/`, via pytest's `tmp_path` — **never** `data/srs.db` or
`data/srs2.db`), `user_id`/`client` (a known user_id pre-seeded into the TestClient's cookie
jar via `store.ensure_user()`, so direct-DB "rig this precondition" helpers know which
user_id to target), and `second_user_id`/`second_client` (a second identity against the same
`store`, for isolation tests).

`create_api(store)` in `api.py` is the factory that makes this possible — it builds a fresh
FastAPI app bound to whatever store you pass it, independent of the module-level `api`
object (which is bound to the real local/production store and is what `main.py` imports).
Tests should always go through `create_api()` + a temp store, never import the module-level
`api`/`store`/`engine` directly.

When adding engine logic, prefer testing it through the HTTP layer (`TestClient` +
`create_api(store)`) over calling `SrsEngine` methods directly — the existing suite rigs
preconditions via direct DB writes (`_set_progress`/`_rig_lesson_item` in `test_lessons.py`)
specifically so assertions can still go through the real API and catch response-shape /
HTTP-layer bugs, not just engine-internals bugs.

## Deployment target

Railway, via Nixpacks (`nixpacks.toml`) + `Procfile`, mirroring the sibling `barobeaver`
project's setup. Not yet connected — see the handoff doc under `planning/` for exactly
what's left before that happens.
