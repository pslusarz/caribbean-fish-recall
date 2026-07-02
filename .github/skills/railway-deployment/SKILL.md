---
name: railway-deployment
description: When interacting with Railway, deploying the app, or when user asks for checking something on the remote.
---

**Status: GitHub done, Railway not yet configured.** Repo is live at
https://github.com/pslusarz/caribbean-fish-recall (public, default branch `main`), pushed
2026-07-02. Railway project/service creation is the next step — that's the deliberate pause
point after local verification (see planning/CURRENT-EPIC-LUSTERECZKO-MIGRATION-IN-PROGRESS.md).
Do not attempt to run `railway` CLI commands against a project/service name until this
section has been filled in with real values below, mirroring barobeaver's version of this
file.

## Once configured, fill in:
- **Railway Project Name:** TBD
- **Service Name:** TBD
- **Default Environment:** TBD (barobeaver uses `staging`)
- **Build System:** Nixpacks (already configured via `nixpacks.toml` and `Procfile`, copied from barobeaver)
- **Staging url for the app:** TBD

## Setup checklist (what's still needed)
1. ~~Create a GitHub repository for this project and push the local git history.~~ Done.
2. Create a Railway project (or a new service inside an existing project) and connect it to the GitHub repo.
3. Attach a Postgres plugin to the service so Railway injects `DATABASE_URL` — the app already reads that env var and falls back to local SQLite when it's absent (see `app/implementation/stores/srs_store.py`).
4. Set up branch-based deploys (staging branch -> staging environment, main -> production), matching barobeaver's setup. Note: no `staging` branch exists in the repo yet — barobeaver's `staging` branch isn't visible via `git branch -a` either, so it's likely created at Railway-connection time, not pre-existing.
5. Run `scripts/reset_db.py` (or let the app's own `seed_if_empty` do it automatically on first request) against the new Postgres database to seed species/photos data. Per-user progress starts empty for everyone by design (cookie-assigned identity, no accounts yet) -- `data/srs.db`, the pre-multi-user single-user database, is kept around locally in case a real progress migration is ever built, but nothing currently reads it.
6. Come back and fill in the project/service names above, plus the log-fetching command from barobeaver's skill file:
   `railway link -p <project> -s <service> -e <environment> && railway logs --latest --lines 100`

## Railway documentation
Consult latest documentation via web search when something isn't working or when setting something up for the first time — best practices may have changed. Good starting point: https://docs.railway.com/guides/fastapi
