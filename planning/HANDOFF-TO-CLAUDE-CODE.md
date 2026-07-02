# Handoff: moving this project from Cowork to Claude Code

Written at the point Paul decided to continue this project in Claude Code instead of
Cowork. This doc is a snapshot of exactly where things stand and what's next — durable
architecture/conventions knowledge went into `CLAUDE.md` at the project root instead;
this file is meant to be read once, acted on, and then it's fine for it to go stale.

## Exact current state

- **Multi-user refactor is complete, tested, and confirmed working against Paul's real
  local dev server.** Schema split into `species`/`progress`/`users`, cookie-based identity,
  ownership checks on lesson/item access, 17 passing tests including cross-user isolation.
  Full narrative is in `planning/CURRENT-EPIC-LUSTERECZKO-MIGRATION-IN-PROGRESS.md`.
- **Local dev database is `data/srs2.db`**, not `srs.db` — confirmed clean via read-only
  inspection: 58 species, 58 progress rows, 1 completed lesson, `rank_history` logging
  correctly, no errors in `data/app.log`.
- **Git has zero commits.** Everything is `git add`-ed (staged) but never committed — Paul
  ran `git init && git add -A` himself early on (the agent sandbox couldn't do this due to a
  filesystem permissions quirk that is Cowork-specific and won't apply in Claude Code — see
  below), but a commit was never made. Several files have since been modified again after
  that initial `git add`, so a fresh `git add -A && git commit` is needed, not just `git
  commit`.
- **No GitHub remote configured** (`git remote -v` is empty). No repo has been created on
  GitHub yet.
- **No Railway project/service exists yet.** `.github/skills/railway-deployment/SKILL.md`
  still has `TBD` placeholders for project name, service name, environment, and staging URL.
  Explicitly marked "not yet configured" — don't run `railway` CLI commands until that file
  has real values.

## What's explicitly deferred (asked about, decided, don't redo the discussion)

- **Legacy single-user progress migration**: `data/srs.db` (pre-multi-user, real single-user
  SQLite file with Paul's actual level/streak history from the original lustereczko
  prototype) is kept on disk but nothing reads it anymore. Paul explicitly chose "start
  fresh, don't worry about it" when asked whether to migrate it into the new schema. If this
  ever comes up again: the data is still there and intact (verified multiple times), a
  migration script could read it and backfill a `progress` row for a specific `user_id`, but
  there's no way to know *which* user_id it should become until there's a real identity
  system (email/login-link) — don't build this until that exists.
- **Cookie security**: plain unsigned uuid4, chosen deliberately over a signed cookie for
  simplicity. `users.email`/`last_login_at` exist unused, reserved for a future login-link
  flow. Not currently planned work — nobody has asked for it yet.
- **`confusion`/`photos` staying global instead of per-user**: flagged as a known
  simplification (one user's wrong answers currently influence distractor selection for
  everyone), explicitly not fixed, no plan to fix unless it becomes a real problem.
- **Stats UX after a first lesson**: a flawless first lesson currently shows "0% rank, 0
  mastered" because promotion requires two consecutive correct answers and a single lesson
  only gives each new fish one real quiz attempt (see `CLAUDE.md`'s SRS mechanics section
  for the full explanation). This was investigated and confirmed as expected/correct
  mechanics, not a bug — Paul was offered a UX fix (surfacing streak progress or a "seen"
  count more prominently) and explicitly said "no, that's fine." Don't unprompted-fix this.

## Immediate next steps (pick one)

1. **Commit + push**: `git add -A && git commit -m "..."`, create a GitHub repo, add it as
   remote, push.
2. **Railway setup**: create project/service, attach Postgres plugin (`DATABASE_URL` gets
   injected automatically — `resolve_database_url()` already handles the
   `postgres://`→`postgresql+psycopg://` rewrite), connect to the GitHub repo once it exists,
   set up branch-based deploys matching `barobeaver`'s pattern (staging branch → staging
   environment, main → production). Full checklist already in
   `.github/skills/railway-deployment/SKILL.md`.
3. Fill in the real project/service names in that skill file once Railway is set up, plus
   the `railway logs` command from barobeaver's equivalent skill file.

None of this has technical blockers — it's just sequencing (repo before Railway, obviously)
and needs Paul's GitHub/Railway account access, which the agent doesn't have.

## Cowork → Claude Code: what changes about how the agent should work here

This matters because a lot of workarounds during this project existed specifically because
of Cowork's sandboxed execution model, and **don't apply once running natively via Claude
Code on Paul's actual Mac**:

- Cowork's bash tool ran in an isolated Linux sandbox with the project folder mounted over
  fuse. Writing SQLite transactions to `data/srs2.db` from that sandbox while Paul's own
  local dev server (on his real Mac) held the same file open caused a `disk I/O error` and a
  stale rollback journal once — a cross-machine-write race condition. **Claude Code running
  directly on Paul's Mac doesn't have this boundary** — it can read/write the local database
  the same way any other local process can, no special caution needed beyond the normal
  "don't fight a process that has the file open" concern.
- Similarly, the agent could not `kill` or `start` Paul's local server process from Cowork's
  sandbox (different machine entirely) — every server restart required giving Paul exact
  terminal commands to run himself. **Claude Code can run `./scripts/start_local.sh`
  directly** and doesn't need to hand this back to the user.
- The one Cowork-specific gotcha that might still be worth knowing: early in this project,
  `git init`/`git add`/`git commit` failed with `Operation not permitted` when run from the
  Cowork sandbox against this specific mounted folder (files could be created/overwritten
  but not deleted in some cases) — Paul ran the initial `git init && git add -A` himself in a
  real terminal to work around it. This is very unlikely to recur under Claude Code, but if
  a fresh `git commit` inexplicably fails, that history is worth knowing about.
