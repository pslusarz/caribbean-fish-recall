# CURRENT: Migrate Caribbean Fish Recall from lustereczko prototype to standalone Railway app

## Goal

The original app was built as a lustereczko/Cowork prototype: a set of Python
"custom tools" (`fish_get_image`, `srs_init_db`, `srs_lesson`) called from a
single `display_ui_to_user` HTML/JS blob, with SQLite as the only datastore
and images shipped as base64 data URIs (required by the lustereczko bridge's
payload model). This epic migrates that into a real, independently
deployable web app on Railway, following the same FastAPI + FastHTML + uv +
nixpacks pattern as `barobeaver`.

## Task: FINISHED — Scaffold project + migrate seed data/photos

Created `/Users/pjs/code/caribbean-fish-recall` with the barobeaver layout
(`app/implementation`, `app/tests`, `planning`, `scripts`, `public`, `data`).
Added `pyproject.toml` (fastapi, python-fasthtml, sqlalchemy, psycopg[binary],
uvicorn, python-dotenv — same dependency set as barobeaver minus httpx/tzdata
which that project needed for NWS calls and this one doesn't), `nixpacks.toml`
+ `Procfile` (byte-identical pattern, just repointed at this module path),
and `.gitignore` (copied verbatim — `data/` stays untracked).

## Task: IMPLEMENTATION — Migrate seed data and photos into new project

In progress: copying `seed.json` + `photo_manifest.json` into
`app/implementation/seed_data/` (committed — used to bootstrap a fresh DB
anywhere), all 141 `photos_web2/*.jpg` files into `public/photos/`
(committed static assets, replacing the base64-data-uri approach), and the
*live* `srs.db` (the user's real in-progress stats) into `data/srs.db` as
the local dev starting point.

## Task: FINISHED — Store layer (SQLAlchemy Core, sqlite-local / postgres-via-DATABASE_URL)

Ported the sqlite3-specific schema from `srs_init_db` into
`app/implementation/stores/srs_store.py` using SQLAlchemy Core: `DATABASE_URL`
unset → falls back to `sqlite:///data/srs.db`; set → rewrites
`postgres://`/`postgresql://` to `postgresql+psycopg://`. All 7 tables
(fish, confusion, meta, lessons, lesson_items, rank_history, photos) defined
as Core `Table` objects so `create_all` works against either backend.
`seed_if_empty()` bootstraps from the checked-in seed JSON only when the
fish table is empty (row-count check, not file-existence, so it works
identically against Postgres).

## Task: FINISHED — Port srs_lesson engine logic

Moved the lustereczko `srs_lesson` tool's logic (Levenshtein spelling
grading, confusion-weighted distractors, REVIEW_GAP/DECAY_GAP split,
70%-target lesson seeding, promoted/demoted/mastered_now flags, rank score +
history) into `app/implementation/srs_engine.py`. Every query rewritten from
positional `?` + sqlite3 cursor to named `:param` + SQLAlchemy `text()`;
every `INSERT OR IGNORE`/`OR REPLACE` rewritten to standard `ON CONFLICT`
(works identically on SQLite 3.24+ and Postgres, so no dialect branching
needed). Methods return plain dicts — no more `json.dumps(...)` wrapper.

## Task: FINISHED — FastAPI routes + static photo serving

Added `/api/lesson/start`, `/api/lesson/next_item`, `/api/lesson/submit`,
`/api/stats`, `/api/browse` in `app/implementation/api.py` (a `FastAPI()`
app mounted at `/api` on the FastHTML app). Photos are served as plain
static files under `/photos/` via FastHTML's `static_path="public"` — no
more base64 data-uri round trip.

## Task: FINISHED — Port frontend

Reused the lesson/stats/browse UI almost verbatim in
`app/implementation/components/index_page.py`. Only real changes:
`callTool()` → `fetchApi()` (plain `fetch('/api/...')`), and the photo
gallery now sets `<img src="/photos/{file}">` directly instead of awaiting a
`fish_get_image` round trip.

## Task: FINISHED — Local dev scripts + docs

Added `scripts/start_local.sh` (barobeaver's pattern, port 5002 instead of
5001 so both can run side by side), `scripts/reset_db.py` (equivalent of
`srs_init_db(reset=True)`, with a confirmation prompt since it wipes
progress), `scripts/verify_db_content.py`. Wrote
`planning/LUSTERECZKO-TO-RAILWAY-MIGRATION-GUIDE.md` — the generalized,
app-agnostic version of this process, intended to become a lustereczko
skill. Also added `.github/skills/{terminal,running-server-locally,
railway-deployment}` mirroring barobeaver's, with the railway-deployment one
explicitly marked "not yet configured" pending the pause point below.

## Task: FINISHED — uv sync + local verification

`uv sync` installed cleanly (Python 3.14, since only `>=3.13` is pinned;
Railway's nixpacks.toml pins exactly `python313` so production will use
3.13 specifically). Verified via curl, end to end, against the migrated
SQLite database seeded from the user's actual in-progress `srs.db`:
- `GET /` → 200, real FastHTML page (title, htmx script tags present)
- `GET /photos/bar-jack_1.jpg` → 200, `content-type: image/jpeg` (confirms
  static serving replaced the base64 hack)
- `GET /api/browse` → 200, all 58 fish with correct multi-photo lists
- `GET /api/stats` → 200, real numbers (26.7% rank, by_level breakdown)
- `POST /api/lesson/start` → 200, valid lesson plan
- `GET /api/lesson/next_item` → 200, valid item incl. photos/streak/mastered
- `POST /api/lesson/submit` → 200 on a pending item, 400
  (`{"detail": "item not pending"}`) on a re-submit of an already-done item
  — confirms the FastAPI `HTTPException` error path works too, not just the
  happy path.

Note: this verification ran in a scratch copy under `/tmp`, not directly in
the mounted project folder — see the gotcha below.

## Task: PARTIALLY DONE — git init + pause for GitHub/Railway connection

All source files are correctly and completely in place at
`/Users/pjs/code/caribbean-fish-recall` (verified: all `.py` files present,
141 photos, seed_data, the untouched real-progress `data/srs.db` snapshot
unmodified by local testing). However, `git init`/`git add`/`git commit`
run against this specific mounted folder hit a filesystem quirk (see
gotcha below) and could not be completed from within the agent's sandbox.
**Action needed from the user**, in a real Terminal on their Mac (not
through the agent sandbox):
```
cd ~/code/caribbean-fish-recall
rm -rf .git   # clears the partial/locked git state left by the sandbox attempt
git init
git add -A
git commit -m "Initial commit: migrate Caribbean Fish Recall from lustereczko prototype to FastAPI+FastHTML"
```
Once that's done, this is the pause point: GitHub repo creation, Railway
project/service setup, and Postgres provisioning still need to happen, and
`.github/skills/railway-deployment/SKILL.md` needs the real project/service
names filled in once those exist.

## Task: FINISHED — Test suite ahead of the multi-user refactor

Added `app/tests/conftest.py` (a `store` fixture pointing at a temp-file
SQLite DB seeded from the real `seed_data/`, plus a `client` fixture) and
`app/tests/test_lessons.py` (14 tests covering lesson planning, intro/
reinforce item flow, promotion/demotion/mastery, spelling-tolerance
grading, the retry pass, resubmission rejection, stats, and browse).
Enabled by a small `api.py` refactor: `create_api(store)` factory extracted
out of the module-level `api` app, so tests instantiate a fresh FastAPI app
bound to an isolated temp database instead of the real one — `data/srs.db`
is never opened by the test suite. Verified stable across 15 repeated runs
despite the engine's unseeded `random.shuffle` calls.

Writing these tests surfaced two real, previously-latent bugs in the
SQLAlchemy port (never triggered before because the real `data/srs.db` was
copied wholesale from the old prototype, so `seed_if_empty` and a
from-scratch `lessons` row had never actually executed against this
codebase):
- `Column(..., default=0)` is a Python/Core-insert-time default, not a DDL
  default — it silently no-ops against this project's raw `text()` INSERTs.
  `fish.level`/`next_due_at`/`mastered`/etc. and `lessons.correct_count`/
  `wrong_count` were landing as `NULL` on any brand-new row (and
  `NULL + 1` stays `NULL`, so lesson summaries would show `null` forever).
  Fixed by switching every such column in `srs_store.py` to
  `server_default=text(...)`, a real DDL default that applies regardless of
  which columns an INSERT lists.
- `submit()` never wrote `fish.mastered_at` — `mastered_now` was reported
  correctly in the API response, but the timestamp column stayed `0`
  forever. Fixed in `srs_engine.py`.

Both fixes only affect newly-created rows (existing databases, including
the real `data/srs.db`, aren't retroactively altered by `create_all()`), so
this matters most for the eventual from-scratch Postgres database and any
future multi-user rows — exactly the thing this test suite exists to
protect ahead of that refactor.

## Task: FINISHED — Multi-user schema + cookie identity

Implemented the low-friction identity model Paul proposed: no login, a
random uuid4 assigned via an httponly cookie (`fr_uid`) on first visit,
`users.email`/`last_login_at` reserved but unused until a "send me a login
link" flow exists later.

Schema (`srs_store.py`): split the old single `fish` table into `species`
(global static facts: name, scientific_name, size, features, photo_file,
mnemonic) and `progress` (per-user SRS state, composite PK
`(user_id, fish_id)`, server_default-zeroed so a new user's row starts
correctly). Added `users` (id, email, created_at, last_login_at,
lessons_completed). Added `user_id` to `lessons` and `rank_history`. Removed
the `meta` table -- `lessons_completed` is now a column on `users`.
`confusion`/`photos` deliberately stayed global (shared signal across
users, not per-account) -- noted as a call worth revisiting later since a
user's wrong answers currently bump distractor weighting for everyone.
Added `SrsStore.ensure_user(user_id, email=None)`: idempotent upsert of the
user row; on first creation only, backfills a zeroed `progress` row per
species (looped VALUES-based upserts -- SQLite's upsert clause turned out
not to support `INSERT ... SELECT ... ON CONFLICT`, only
`INSERT ... VALUES ... ON CONFLICT`) plus an initial `rank_history` point.

`srs_engine.py`: every method now takes `user_id` and every query is scoped
by it. Two correctness issues specifically worth flagging since they're the
kind of thing that's easy to get wrong in a multi-user retrofit and would
have shipped silently without the isolation tests below: `start()`'s
"abandon any other active lesson" cleanup was originally unscoped
(`WHERE status='active'`) -- without adding `AND user_id=:uid` it would
have abandoned *every* user's in-progress lesson every time anyone started
a new one. `next_item`/`submit` now verify the requested lesson/item
actually belongs to the calling user_id (via a JOIN against `lessons`)
before doing anything with it, rather than trusting a caller-supplied
integer id -- otherwise one user could read or answer into another user's
lesson just by guessing/incrementing ids.

`api.py`: added `get_user_id(request, response)` as a FastAPI dependency,
wired into every route. Plain uuid4, no signing (accepted tradeoff for a
personal trainer app -- copying someone's cookie value lets you act as
them; revisit if/when the login-link flow adds real accounts).

Test suite: `app/tests/conftest.py` now has `user_id`/`client` and
`second_user_id`/`second_client` fixture pairs -- both pre-seed the
TestClient's cookie jar with a known id (via `store.ensure_user()`) before
any request happens, since rigging a precondition directly in the DB needs
to know which user_id the client will actually be. `test_lessons.py` grew
from 14 to 17 tests: 3 new ones specifically assert cross-user isolation
(independent progress, can't read/answer another user's lesson, starting a
lesson doesn't abandon someone else's active one). All 17 pass, stable
across 15 repeated runs.

**Real-world gotcha worth remembering for next time**: Paul had his local
dev server running (`start_local.sh`, uvicorn `--reload`) against the real
`data/srs.db` while this refactor was happening. Editing `srs_store.py`
hot-reloaded his live server mid-session, which ran `metadata.create_all()`
+ `seed_if_empty()` against his *real* database -- creating the new
`users`/`species`/`progress` tables there (harmless/additive; nothing was
dropped) and seeding `species` fresh. His actual progress is untouched and
still sitting in the old `fish` table (verified: 58 rows, real
level/seen_count/correct/wrong values, `meta.lessons_completed=12`), but
the new `progress`/`users` tables were empty at last check, meaning the
next time he opens the app he'll get a brand-new fresh account under the
new schema -- exactly what he asked for ("start fresh, don't worry about
it"), just triggered a little earlier/more silently than a deliberate
"flip the switch" moment would have been. Worth remembering for the future
skill: **agent file edits apply to the user's real filesystem immediately,
and if they have a hot-reloading dev server pointed at the same folder,
schema edits take effect on their live database the instant the file is
saved** -- not something a temp-database test suite protects against.

## Gotcha encountered this session (folded into the migration guide)

The agent's sandbox mount of this project folder reliably supports creating
new files (confirmed: 167 files synced correctly, server ran and served
requests when tested from a `/tmp` scratch copy with the same file
contents) but not *deleting* files that already exist on it in certain
circumstances — attempts to `rm -rf .venv` (after a failed in-place `uv
sync`) and later `.git` (after `git init`/`git add`) both failed with
`Operation not permitted`, even though `ls -la` showed the current session's
own uid owning the files with normal permission bits. Worked around it by
doing all `uv sync`/server-verification work in a `/tmp` scratch copy (fully
writable) and `rsync`-ing only the finished, correct source files back —
this only works for *creating/overwriting* files, not for deleting stale
ones on the mount, so `.venv`/`.sesskey`/a partial `.git` are stuck as inert
(but harmless and gitignored) cruft in the folder until the user cleans
them up from their actual Mac Terminal.
