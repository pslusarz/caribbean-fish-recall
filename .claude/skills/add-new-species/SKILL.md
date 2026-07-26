---
name: add-new-species
description: Audit REEF.org's Caribbean gallery for species missing from the app and add them (facts, photos, confusion pairs). Use when the user asks to check for new species, or to add specific ones. REEF adds to that gallery over time, so this is meant to be re-run periodically.
---

## Source of truth, and its quirks

Gallery: https://www.reef.org/species/galleries/caribbean

**Pagination is not reliable — poll it carefully.** The bare URL (page 1, what a visitor
actually lands on) is served from a stale Drupal page cache and can lag behind what pages
2-8 show live: it has previously listed species that appeared nowhere in a live crawl of
`?page=1` through `?page=7`. `?page=0` explicit is a *different*, separately-cached
request from the bare URL — don't substitute one for the other. To get the true full set:
`curl -A "Mozilla/5.0"` the bare URL plus `?page=1` through `?page=7` (8 requests total),
extract `<a href="/species/SLUG">Name</a>`, and union+dedupe by slug (some slugs repeat
across pages — a REEF Views bug, not a sign you're missing pages). Don't use WebFetch/
page-summary tools for this crawl — tried it first and it silently dropped or hallucinated
entries across repeated fetches of the same page. Cross-check candidates against our
`scientific_name`s too, not just display name, before calling something missing — a couple
of near-misses turned out to already be in the app under slightly different phrasing.

## Per-missing-species: pull facts + photos from the raw HTML

`curl` `https://www.reef.org/species/SLUG` and parse directly (again, not a summarizer —
it misses exact image URLs and credits):
- Scientific name / Size / Distinctive features: the text following those three labels.
- Photos: each `<h2 class="element-invisible"><a href="/file/...">FILENAME.jpg</a></h2>`
  pairs with the next `field-name-field-artist` div (the credit). Build the download URL
  as `https://www.reef.org/sites/default/files/species_images/FILENAME.jpg` — not any of
  the `styles/WxHpx/public/...` derivative URLs also present on the page.
- Write your own `mnemonic` (REEF doesn't supply one) — short, keyed to the actual
  features, and naming the confusable neighbor if REEF's copy calls one out (e.g. "tail
  squared, not rounded like X's").

## Photos: resize before converting

`cwebp -q 82 -resize 1024 0 in.jpg -o public/photos/slug_N.webp` (1-indexed; `-resize
1024 0` caps width at 1024px and preserves aspect ratio, no-ops if already smaller). REEF's
non-derivative URL is sometimes much larger than 1024px — skipping the resize produces an
oversized outlier. Sanity-check against the repo average once done (`ls -la
public/photos/*.webp`, currently ~95KB/file) and re-encode anything far above it.

## Wiring it into the app

- `app/implementation/seed_data/seed.json`: append entries to `"fish"`
  (id/name/scientific_name/size/features/photo_file/mnemonic). id = REEF's slug as-is;
  check it's not already taken. Only add to `"confusion_pairs"` when there's a real
  reason (REEF's own text cross-references a look-alike, or an existing pair already
  covers the same family/genus) — don't force pairs just for coverage.
- `app/implementation/seed_data/photo_manifest.json`: append one entry per species
  (file/credit/orig_url/web_file per photo).
- Never hand-edit anything else — species count is read live everywhere (`stats.total`,
  browse, lesson summary). If you find a hardcoded count anywhere, that's a regression;
  fix it to read live rather than updating the number.

## Verify before deploying

`uv run pytest app/tests/` should need zero changes to pass. That's necessary but not
sufficient — also seed a scratch DB and run the new species through several real lesson
cycles (start → intro → reinforce → MC/spelling → submit) with correct answers, confirming
each reaches level 4/mastered with no exceptions. A fish silently excluded by an `INNER
JOIN` against a missing `progress` row won't show up any other way.

`scripts/add_missing_species.py` is the generic existing-users migration (backfills a
zeroed `progress` row per existing user for whatever's newly in `seed.json`) — it's already
written to need no changes for a new batch, just run it against whichever DB you're
targeting.

## Deploying

Production auto-deploys the instant `origin main` gets a push (see `railway-deployment`),
so there's no separate approval gate on that branch — staging has to lead. Commit and push
directly on `staging` (never commit new work on `main` first and fast-forward staging up to
it — that leaves `main` sitting ahead of `origin/main` with unpushed commits for no reason,
which is confusing and easy to accidentally push later). Once staging's deploy succeeds,
run `add_missing_species.py` against staging's DB, then hit the live staging API
(`/api/browse`, a couple of real lesson cycles through it) before calling it done.

**Do not fast-forward `main` to `staging` / push `origin main` as part of this flow
without the user explicitly approving it first.** Leave the verified work on `staging` and
ask; promoting to production is just `git checkout main && git merge --ff-only staging &&
git push origin main` once they say go.
