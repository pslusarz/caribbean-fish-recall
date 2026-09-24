---
name: add-new-species
description: Audit REEF.org's Caribbean gallery for species missing from the app and add them (facts, photos, confusion pairs). Use when the user asks to check for new species, or to add specific ones. REEF adds to that gallery over time, so this is meant to be re-run periodically.
---

## Source of truth, and its quirks

Gallery: https://www.reef.org/species/galleries/caribbean

**Pagination is not reliable — poll it carefully.** Two separate problems:

1. **The default sort is unstable across pages.** Page boundaries shift between requests,
   so one pass over every page returns the right number of *rows* but many duplicates and
   silently misses species. As of 2026-09 a single pass returned 81 rows but only ~65
   unique slugs. The fix: crawl every page under several explicit sort orders and take the
   union. `?order=title&sort=asc|desc`, `?order=nid&sort=asc|desc` and
   `?order=created&sort=asc` each give a different subset. You're done when the union's
   unique count equals the per-crawl row total (10 per page × full pages + the last page).
2. **The bare URL is served from a stale Drupal page cache** and can lag behind what
   `?page=N` shows live. `?page=0` explicit is a *different*, separately-cached request
   from the bare URL, so include both.

Don't hardcode the page range. Read the last page from the `pager-last` link on page 0
(it was `?page=8` as of 2026-09), and check that the page after it comes back empty.
`items_per_page=All` appears in pager links, but REEF ignores it (still 10 per page).
Use `curl -A "Mozilla/5.0"`, extract `<a href="/species/SLUG">Name</a>`, and sanity-check
that the per-page link count matches the per-page `views-row` count. Don't use WebFetch/
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

## Skip species with incomplete REEF pages; don't fill the gaps yourself

Only add a species when REEF's own page has **all** of: at least one real, credited photo
at the `species_images/` URL; a Size; and Distinctive features. The gallery includes some
that don't meet this bar. Leave those out and list them for the user with the reason. Don't
write the facts from general knowledge or pull photos from elsewhere to make up the
difference. The user confirmed this (2026-09): an ID trainer is only as good as its photos
and facts, and REEF being the single source of truth is the point.

Seen so far, and why skipped (expect these to still be missing on the next audit; check
whether REEF has completed their pages, and don't re-report them as "new"):
- **African Pompano** (`african-pompano`): facts present, but no photo on the page.
- **Atlantic Wolffish** (`atlantic-wolffish-0`): a cold-water North Atlantic species that
  looks misfiled in the Caribbean gallery. It has photos, but no size or features.
- **Cherubfish** (`cherubfish`) and **Roughhead Blenny** (`roughhead-blenny`): legacy stub
  pages. Each has a single uncredited 198×132 thumbnail (`species/TWA/NNN.jpg`, not
  `species_images/`), and no size or features.

The app's species count will therefore sit below REEF's gallery total (77 vs. 81 as of
2026-09), which is expected. Say so up front when reporting, so the gap isn't mistaken for
a pending migration.

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

## Data migration: still manual, for staging and production both

`uv run pytest app/tests/` should need zero changes to pass, and already covers the risk
this workflow creates — `test_lessons.py`'s
`test_brand_new_user_automatically_gets_a_species_added_after_other_users_signed_up` and
`test_existing_user_gets_a_new_species_only_after_add_missing_species_runs` prove a
brand-new signup automatically gets whatever's currently in `species`, while an
already-existing user only gets a newly-added one after `scripts/add_missing_species.py`
runs against their database. That's the whole reason it's safe to skip manually seeding a
scratch DB and re-proving this by hand each time.

Passing tests don't touch the real staging/production databases, though — deploying the
code is not the same as migrating the data. `add_missing_species.py` is already written to
need no changes for a new batch; after each deploy below, run it against that environment's
DB before considering the species actually live there.

## Deploying

Production auto-deploys the instant `origin main` gets a push (see `railway-deployment`),
so there's no separate approval gate on that branch — staging has to lead. Commit and push
directly on `staging` (never commit new work on `main` first and fast-forward staging up to
it — that leaves `main` sitting ahead of `origin/main` with unpushed commits for no reason,
which is confusing and easy to accidentally push later). Once staging's deploy succeeds,
run the data migration above against staging's DB, then hit the live staging API
(`/api/browse`, a couple of real lesson cycles through it) before calling it done.

**Do not fast-forward `main` to `staging` / push `origin main` as part of this flow
without the user explicitly approving it first.** Leave the verified work on `staging` and
ask; promoting to production is just `git checkout main && git merge --ff-only staging &&
git push origin main` once they say go — then run the data migration a third time, against
production's DB.
