# Caribbean Fish Recall

A lesson-based spaced-repetition trainer for identifying 58 Caribbean reef fish
species, migrated from a lustereczko/Cowork prototype into a standalone
FastAPI + FastHTML web app deployable on Railway.

See `planning/` for the migration plan and step-by-step log, and
`.github/skills/` (once added) for operational runbooks. Project layout
mirrors the `barobeaver` project:

- `app/implementation` — production code (FastAPI routes, FastHTML page shell,
  SQLAlchemy store layer, ported SRS engine)
- `app/tests` — unit and integration tests
- `planning` — migration plan + progress log
- `scripts` — local dev server script, DB seed/verify scripts
- `public` — static assets (fish photos, favicon)

## Local development

```bash
uv sync
./scripts/start_local.sh
```

The app defaults to a local SQLite database at `data/srs2.db` when no
`DATABASE_URL` environment variable is set (`data/srs.db`, if present, is
the pre-multi-user database and is no longer read by the app -- see
planning/ for why). On Railway, `DATABASE_URL` is
provided by the attached Postgres plugin and used automatically (with the
`postgresql://` → `postgresql+psycopg://` rewrite needed for SQLAlchemy +
psycopg3, same as `barobeaver`).
