---
name: railway-deployment
description: When interacting with Railway, deploying the app, or when user asks for checking something on the remote.
---

**Status: fully configured.** GitHub repo, Railway project, both environments, Postgres,
and public domains are all live as of 2026-07-02. Repo:
https://github.com/pslusarz/caribbean-fish-recall (public, default branch `main`).

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
Species/photos seed automatically on first request (`seed_if_empty`) — both live
deployments already returned `"total":58` on `/api/stats` immediately after their first
deploy, so no manual `scripts/reset_db.py` run against production was needed. Re-run it
only if you need to force a reseed.

## Railway documentation
Consult latest documentation via web search when something isn't working or when setting
something up for the first time — best practices may have changed, and some behaviors
(see CLI quirks above) aren't documented at all. Good starting point:
https://docs.railway.com/guides/fastapi but feel free to go beyond it.
