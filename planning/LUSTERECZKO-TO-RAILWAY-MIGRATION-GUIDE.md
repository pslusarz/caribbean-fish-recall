# Migrating a lustereczko/Cowork prototype to a standalone Railway app

This documents the general pattern used to migrate the Caribbean Fish Recall
app from a lustereczko/Cowork prototype (custom tools + a single
`display_ui_to_user` HTML/JS blob) into a standalone FastAPI + FastHTML app
deployable on Railway. Written to be generalizable — the goal is to fold
this into a lustereczko-specific skill later, so record the *pattern*, not
just what we did for fish.

## Why this migration is usually mechanical, not risky

A lustereczko custom tool is already, structurally, a stateless function
that reads/writes a datastore and returns a JSON-serializable dict. That's
exactly what a FastAPI route handler is. The porting work is almost entirely
mechanical:

| lustereczko concept | Railway-app equivalent |
|---|---|
| `add_custom_tool(name, code)` where `code` defines `run(**kwargs)` | A FastAPI route handler, same function body |
| `run_custom_tool(name, args)` from the frontend, via `window.app.callServerTool({name:'run_custom_tool', arguments:{name, args}})` | Plain `fetch('/api/...')` from the frontend |
| `return json.dumps(some_dict)` (mandatory — bare dicts break client-side `JSON.parse`) | `return some_dict` — FastAPI serializes dicts automatically, no gotcha to remember |
| `sqlite3.connect(DB_PATH)` with a hardcoded absolute path | A store class using SQLAlchemy Core with `DATABASE_URL` env var + a local-sqlite fallback (see below) |
| Images returned as base64 `data:` URIs (forced by the lustereczko bridge's per-call payload limit) | Plain static files served at a URL, `<img src="/photos/foo.jpg">` — no encoding round-trip, no size ceiling |
| `display_ui_to_user(html_fragment)` — one big HTML blob rendered in an iframe | A page-shell route (FastHTML) that returns the *same* HTML/JS almost verbatim, just not iframe-constrained |

## Step-by-step

1. **Scaffold the project from a known-good sibling app**, don't start from
   a blank template. If you (or the user) have a previous FastAPI+FastHTML
   Railway app, copy its `pyproject.toml`, `nixpacks.toml`, `Procfile`,
   `.gitignore`, and `scripts/start_local.sh` verbatim, adjusting only the
   module path (`app.implementation.main:app`) and the local dev port (pick
   a different port than any sibling app that might be running at the same
   time). This guarantees the deploy plumbing (build system, start command)
   is already proven to work on Railway, so the migration's only real risk
   is the application logic, not the infrastructure.

2. **Extract the custom tools' business logic into a plain class**, keeping
   constants (timing windows, thresholds, seed data shape) unchanged. Change
   only: (a) drop every `json.dumps(...)`/`json.loads(...)` wrapper — return
   dicts directly; (b) replace the sqlite3 cursor with a SQLAlchemy
   `Connection`; (c) replace positional `?` placeholders with named `:param`
   bind params (works identically against SQLite and Postgres); (d) replace
   sqlite-specific `INSERT OR IGNORE`/`INSERT OR REPLACE` shorthand with
   standard `ON CONFLICT (...) DO NOTHING` / `DO UPDATE SET ...` — this exact
   syntax is supported identically by SQLite (3.24+) and Postgres, so no
   dialect branching is needed anywhere in the query layer.

3. **Build the store layer before the business logic**, following this
   pattern (lifted from barobeaver's `station_store.py`):
   ```python
   def resolve_database_url(database_url=None):
       db_url = database_url or os.environ.get("DATABASE_URL")
       if not db_url:
           return f"sqlite:///{os.path.join('data', 'app.db')}"
       if db_url.startswith("postgres://"):
           return db_url.replace("postgres://", "postgresql+psycopg://", 1)
       if db_url.startswith("postgresql://"):
           return db_url.replace("postgresql://", "postgresql+psycopg://", 1)
       return db_url
   ```
   Railway's Postgres plugin injects `postgres://` or `postgresql://`, which
   SQLAlchemy + psycopg3 needs rewritten to `postgresql+psycopg://` — this
   one-line fix is the entire cross-environment portability story. Define
   tables with SQLAlchemy Core `Table()`/`Column()` (not the ORM) so
   `metadata.create_all(engine)` works against either backend without
   per-dialect DDL.

4. **Decide what "seed data" means and split it from "live progress".**
   The lustereczko version's SQLite file mixed both permanently. For the
   export: put the bootstrap data (base entities, static relationships) in a
   committed JSON file under the app package (e.g. `seed_data/`), loaded by
   a `seed_if_empty()` method keyed off row count (not file existence, since
   that check needs to work the same way against Postgres). Optionally,
   *also* copy the live SQLite file with real accumulated progress into the
   gitignored local `data/` dir, purely as a convenience starting point for
   local dev — never commit that file.

5. **Kill the base64-image hack.** It only existed because
   `run_custom_tool`'s response has to fit in one call's payload, and
   `display_ui_to_user` fragments can't reference arbitrary local files.
   Once you're not inside that bridge, just serve the image directory as
   static files (`fast_app(static_path="public")` in FastHTML, or
   `StaticFiles` in plain FastAPI) and point `<img src>` directly at it.
   This also fixes the "had to pre-resize everything to stay under a
   1.3M-character single-call limit" constraint — you can serve full-size
   originals if you want, since there's no per-call payload ceiling anymore.

6. **Split the frontend's `callTool()` into two things**: a FastAPI app
   with one route per former custom tool action, mounted under a prefix
   (e.g. `/api`) on the FastHTML app (`app.mount("/api", api)` — both are
   Starlette-based ASGI apps so this just works); and a page-shell route in
   FastHTML that returns the *existing* HTML/JS almost unchanged. The only
   required JS edits are: `window.app.callServerTool(...)` → `fetch(...)`,
   and any `fish_get_image`-style base64 tool call → a direct static URL.
   Everything else — DOM structure, styling, client-side state machine —
   can be ported verbatim.

7. **Verify locally before touching git or Railway at all.** Use the
   sibling app's `start_local.sh` pattern (port cleanup, log rotation,
   5-second startup health check) and curl every route explicitly,
   including a non-trivial end-to-end flow (start → next_item → submit →
   stats), not just `GET /`. Only after this passes should you consider
   git/GitHub/Railway — those steps involve credentials and remote state
   that are much more expensive to undo than local-only mistakes.

8. **Pause before creating the GitHub repo / Railway project.** This is a
   deliberate checkpoint, not a technical requirement — creating remote
   infrastructure is a good moment to get the user's input on naming,
   project/service placement (new Railway project vs. new service in an
   existing project), and branch strategy, rather than assume defaults.

## Gotchas specific to this kind of migration (not just this app)

- **Don't trust a sandbox's mount of a live, externally-open SQLite file for
  writes.** If you're copying a *live* app's database as a local-dev
  starting point (step 4), that's a plain filesystem *read*, which is safe.
  But never try to `cp` a fresh version *back* into a database file that
  some other running process still has open — writes through some sandbox
  filesystem mounts can silently no-op or truncate against a locked file.
  If you ever need to mutate a live app's database in place, do it through
  a tool that runs natively in the same process space as whatever holds the
  file open, and verify with an integrity check before and after.
- **`lastrowid` doesn't exist for Postgres.** If you kept the sqlite
  convention of reading `cursor.lastrowid` after an insert to get a new
  auto-increment ID, you need an `INSERT ... RETURNING id` for Postgres
  instead (branch on `engine.dialect.name == "postgresql"`, or use
  SQLAlchemy Core's `insert()` construct with `.returning()` uniformly if
  you want to avoid the branch entirely).
- **Pydantic request bodies vs. lustereczko's untyped `args` dict.** The
  lustereczko tool signature (`def run(action, lesson_id=None, ...)`) took
  everything as loosely-typed kwargs. A FastAPI route wants a typed request
  model (or query params) — this is a good forcing function to actually
  decide which fields are required per action, but budget a few minutes per
  route for it.
