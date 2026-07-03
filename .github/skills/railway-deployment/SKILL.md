---
name: railway-deployment
description: When interacting with Railway, deploying the app, or when user asks for checking something on the remote.
---

**Status: fully configured. Auto-deploy-on-push is confirmed working as of 2026-07-03.**
GitHub repo, Railway project, both environments, Postgres, and public domains are all live.
Repo: https://github.com/pslusarz/caribbean-fish-recall (public, default branch `main`).
Deploying code is just `git push origin main` (production) / `git push origin staging`
(staging) — Railway's GitHub App builds and deploys automatically, no manual step needed.

**Historical gap, now fixed:** earlier the Railway GitHub App had zero access to this repo
(confirmed via `gh api repos/pslusarz/caribbean-fish-recall/deployments` returning `[]`,
vs. barobeaver's 13 records from `railway-app[bot]`), so pushes silently deployed nothing.
Fixed via GitHub's installation-access UI (https://github.com/settings/installations).
If auto-deploy ever seems to stop working again, re-check that `deployments` endpoint
before assuming it's still fine — it went silently broken once already. The manual
workaround below still works as a fallback if it breaks again.

**Manual deploy workaround** (not needed under normal operation): re-running `railway
service source connect --repo <repo> --branch <branch> --service <service> --environment
<env>` on an already-connected service forces a fresh build of the branch's current HEAD,
even though the CLI reports `error: ServiceInstance not found` (see CLI quirks below — that
error doesn't mean it failed).

You will mostly use the `railway` CLI via the Bash tool to achieve goals here. Auth is
already set up — `railway login` was run once via browser (GitHub OAuth), and the session
persists at `~/.railway/config.json`. No token management needed; just run `railway`
commands directly.

## Project Configuration
- **Railway Project Name:** `caribbean-fish-recall` (id `7a524954-439c-4e3d-80e9-f591c1be11ba`)
- **Build System:** Nixpacks/Railpack, auto-detected (Python 3.13 + uv), via `nixpacks.toml` and `Procfile`
- **Workspace:** Pawel Slusarz's Projects

### production environment
- **Service:** `caribbean-fish-recall` (id `0c517ed9-3a29-4cba-b957-6c8ae4be1cdb`) — tracks `main` branch
- **Postgres:** `Postgres-vCvC` (auto-generated name, don't rename — no CLI rename command exists)
- **URL:** https://caribbean-fish-recall-production.up.railway.app

### staging environment
- **Service:** `caribbean-fish-recall-staging` (id `9bc680d3-8d75-4076-80e2-75ed358d8a07`) — tracks `staging` branch
- **Postgres:** `Postgres` (auto-generated name)
- **URL:** https://caribbean-fish-recall-staging-staging.up.railway.app

Both services have `DATABASE_URL` set as a reference variable
(`${{<postgres-service-name>.DATABASE_URL}}`) pointing at their own environment's Postgres
over Railway's private network — not the public proxy URL. Each environment has a fully
independent Postgres instance; there is no shared database between staging and production.

## Critical architecture note: branch-per-environment requires TWO SERVICES, not one

This bit us during setup and is worth understanding before touching branch config. Railway
binds a GitHub repo + branch to a **service**, and that binding is effectively project-wide
for that service — NOT scoped per-environment the way `railway service source connect
--environment X` implies. Verified empirically: setting the branch on one environment's
instance of a shared service silently changed the branch (and triggered a rebuild) for
*every other environment* that also had an instance of that same service, including one
that had already deployed successfully. barobeaver, the sibling project this was modeled
on, actually never solved this — despite its skill doc claiming a staging→staging,
main→production split, its Railway project turned out (verified live via `railway status
--project fantastic-smile`) to have only a single `staging` environment. No production
environment was ever actually built there.

For this project we chose real isolation: **two separate services**, each with its own
branch binding and its own Postgres, one per environment. That's what's documented above.
If you ever want to collapse back to barobeaver's simpler single-environment model, that's
a legitimate option too — just don't try to get one shared service to track two branches
across two environments, it doesn't work.

## Known CLI quirks
- `railway service source connect` sometimes returns `error: ServiceInstance not found`
  even when the operation actually succeeded. Don't trust the exit code alone — verify with
  `railway status --json` (check `meta.branch` under `activeDeployments`) after a `connect`
  call that errors this way before assuming it failed.
- `railway add --repo ... --branch X` does not reliably apply `--branch` on the *first*
  deploy of a freshly created service (it built from the repo's default branch instead,
  ignoring the flag). If the first deploy comes up on the wrong branch, fix it with a
  follow-up `railway service source connect --repo ... --branch X --service Y`.
- `railway service delete --service X --environment Y` is correctly scoped to that one
  environment's instance — it does not delete the service from other environments that
  share it. This is the mechanism used to split a shared service into environment-specific
  ones (delete the unwanted environment's instance, then `railway add` a fresh
  differently-named service there).

## Diagnosing "the app feels slow"

Don't assume it's app code. First check `railway metrics --service X --environment Y`:
if CPU/memory are near-idle (seen: 0% CPU, 3% memory) but `railway logs --http` shows wild
latency swings on the *exact same static content* (e.g. `GET /` ranging 8ms to 30000ms for
an unchanged page, or `GET /photos/*.jpg` ranging 11ms to 11500ms for plain file reads),
that combination points to a bad container/host, not a code regression — no application
code path is shared between static file serving and DB-backed routes, so if both are
equally erratic, the app isn't the variable.

Confirmed fix (2026-07-02, production): `railway service restart --service X --environment
Y --yes` dropped latency from 6-30s to a steady 0.4-1.0s across page/photo/API requests
immediately. Try this before spending time auditing recent commits for a "regression" that the
static-content evidence already rules out.

## Critical Workflows

### Fetching Remote Logs (Non-Interactive)
```bash
railway logs --service caribbean-fish-recall --environment production --lines 100
railway logs --service caribbean-fish-recall-staging --environment staging --lines 100
```

### Checking deploy/branch state across both environments
```bash
railway status --json | python3 -c "
import json,sys
d = json.load(sys.stdin)
for env in d['environments']['edges']:
    node = env['node']
    print(node['name'])
    for si in node['serviceInstances']['edges']:
        s = si['node']
        for dep in s.get('activeDeployments', []):
            meta = dep.get('meta', {})
            print(' ', s.get('serviceName'), meta.get('branch'), dep.get('status'))
"
```

### Setting/checking variables
```bash
railway variables --service caribbean-fish-recall --environment production --kv
railway variable set 'KEY=value' --service <service> --environment <env> --skip-deploys --json
```

## Seeding data
Species/photos seed automatically on first request (`seed_if_empty`) when the `species`
table is empty — both live deployments seeded themselves on first request after their
initial deploy, so no manual `scripts/reset_db.py` run against production was needed. That
script only helps for a full wipe+reseed; see below for adding data to an already-seeded DB.

## Data migrations (there is no Railway feature for this)

Railway has no migration-runner (unlike e.g. Heroku's release phase), and this project has
no schema-migration tool either (`metadata.create_all()` only creates missing tables, never
alters existing ones — see CLAUDE.md's known-footguns section). Two different situations:

- **Schema changes** (new column/table): not yet safely automated at all. Either hand-write
  the `ALTER TABLE` and run it the same way as below, or adopt Alembic before it's actually
  needed (CLAUDE.md already flags this as coming due once real Postgres data matters).
- **Data changes to already-existing tables** (e.g. adding new seed rows): write a plain,
  idempotent Python script under `scripts/` — `scripts/add_missing_species.py` is a working
  template (adds any seed_data rows not yet present, backfills a zeroed `progress` row per
  existing user for the new rows only, safe to rerun any number of times).

**How to actually run a script like that against a live environment:**

```bash
# railway run injects that environment's variables but executes LOCALLY -- and
# DATABASE_URL there is the *internal* railway.internal hostname, which only
# resolves from inside Railway's own network. This will fail with a DNS error:
railway run --service caribbean-fish-recall --environment production uv run python scripts/foo.py

# Instead, fetch that environment's Postgres's PUBLIC proxy URL and point the
# script at it directly. Capture into a shell variable and never echo it --
# DATABASE_PUBLIC_URL is a live credential, and printing it gets blocked by
# the auto-mode classifier (rightly) if you try:
DB_URL=$(railway variables --service Postgres-vCvC --environment production --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)
DATABASE_URL="$DB_URL" uv run python scripts/foo.py
unset DB_URL
```

Postgres service names differ per environment: `Postgres-vCvC` for production,
`Postgres` for staging (see Project Configuration above). Always dry-run against local
SQLite first, and re-run the same script a second time against the target DB to confirm
it's actually idempotent before trusting it against production.

## Railway documentation
Consult latest documentation via web search when something isn't working or when setting
something up for the first time — best practices may have changed, and some behaviors
(see CLI quirks above) aren't documented at all. Good starting point:
https://docs.railway.com/guides/fastapi but feel free to go beyond it.
