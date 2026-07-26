"""
Lesson-flow tests for the FastAPI API, run against a temporary SQLite
database (see conftest.py) -- never against data/srs.db or data/srs2.db.

These exercise the multi-user engine (cookie-assigned user_id, species vs.
progress split) as it exists today, so they double as a safety net for
future changes: if these still pass, per-user behavior and isolation were
preserved.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.implementation.api import COOKIE_NAME, create_api
from app.implementation.srs_engine import PROMOTE_THRESHOLD
from scripts.add_missing_species import add_missing_species


def _rig_lesson_item(store, user_id, fish_id, level_at_plan, is_retry=0):
    """Directly insert a lessons/lesson_items row pointing at `fish_id` for
    `user_id`, so a test can hit engine.submit() with a precise precondition
    (e.g. "this fish is already at streak_success=1") without having to
    first fight the randomized lesson-selection algorithm into producing
    that exact state. Returns (lesson_id, item_id)."""
    with store.engine.begin() as conn:
        lesson_id = conn.execute(
            text(
                "INSERT INTO lessons (user_id, started_at, planned_size, target_rate, status) "
                "VALUES (:uid, 0, 1, 0.7, 'active')"
            ),
            {"uid": user_id},
        ).lastrowid
        item_id = conn.execute(
            text(
                "INSERT INTO lesson_items (lesson_id, seq, fish_id, level_at_plan, is_retry, status) "
                "VALUES (:lid, 0, :fid, :lvl, :retry, 'pending')"
            ),
            {"lid": lesson_id, "fid": fish_id, "lvl": level_at_plan, "retry": is_retry},
        ).lastrowid
        return lesson_id, item_id


def _set_progress(store, user_id, fish_id, **fields):
    cols = ", ".join(f"{k}=:{k}" for k in fields)
    with store.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE progress SET {cols} WHERE user_id=:uid AND fish_id=:fid"),
            {**fields, "uid": user_id, "fid": fish_id},
        )


def _get_fish(store, user_id, fish_id):
    with store.engine.begin() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT s.id AS id, s.name AS name, p.level AS level, "
                    "p.streak_success AS streak_success, p.streak_fail AS streak_fail, "
                    "p.seen_count AS seen_count, p.mastered AS mastered, p.mastered_at AS mastered_at, "
                    "p.next_due_at AS next_due_at "
                    "FROM species s JOIN progress p ON p.fish_id = s.id "
                    "WHERE s.id=:fid AND p.user_id=:uid"
                ),
                {"fid": fish_id, "uid": user_id},
            ).mappings().first()
        )


def _get_lesson(store, lesson_id):
    with store.engine.begin() as conn:
        return dict(
            conn.execute(text("SELECT * FROM lessons WHERE id=:lid"), {"lid": lesson_id}).mappings().first()
        )


# ---------- lesson planning ----------


def test_start_lesson_on_fresh_db_is_all_new(client):
    """With every fish at level 0 and nothing ever reviewed, there's no due
    review pool, so the 70%-target algorithm should degenerate to an
    all-new lesson: 15 new (intro) items, one per fish -- no reinforce
    clones anymore, so planned_size matches n_new exactly."""
    resp = client.post("/lesson/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["n_new"] == 15
    assert data["n_review"] == 0
    assert data["planned_size"] == 15
    assert data["fallback_used"] is False


def test_first_item_in_a_fresh_lesson_is_an_intro(client):
    resp = client.post("/lesson/start")
    lesson_id = resp.json()["lesson_id"]
    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
    assert item["ok"] is True
    assert item["done"] is False
    assert item["question_type"] == "intro"
    assert item["level_at_plan"] == 0
    assert item["choices"] is None
    assert item["name"] is not None
    assert item["scientific_name"] is not None


def test_submitting_an_intro_item_is_always_correct_and_promotes_straight_to_level_one(client, store, user_id):
    """A fish's first-ever sighting (the intro card) is always correct and
    immediately promotes it to level 1, in the same lesson -- no separate
    reinforce quiz needed, and it counts toward the lesson's correct tally
    just like any other graded answer (product decision: L0 exposure counts
    toward score)."""
    resp = client.post("/lesson/start")
    lesson_id = resp.json()["lesson_id"]
    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
    assert item["question_type"] == "intro"

    before = _get_fish(store, user_id, item["fish_id"])
    submit = client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": None}).json()

    assert submit["ok"] is True
    assert submit["is_intro"] is True
    assert submit["correct"] is True
    assert submit["promoted"] is True
    assert submit["demoted"] is False
    assert submit["new_level"] == 1

    after = _get_fish(store, user_id, item["fish_id"])
    assert before["level"] == 0
    assert after["level"] == 1
    assert after["seen_count"] == before["seen_count"] + 1
    # regression guard: next_due_at must actually get scheduled here, or this
    # fish becomes permanently invisible to both new_pool (level=0 required)
    # and due_review (next_due_at>0 required) -- confirmed by hand that a
    # naive "level=1 with no next_due_at update" leaves it unreachable forever.
    assert after["next_due_at"] > 0

    lesson = _get_lesson(store, lesson_id)
    assert lesson["correct_count"] == 1
    assert lesson["wrong_count"] == 0


# ---------- promotion / demotion / mastery ----------


def test_promotion_on_second_consecutive_correct_answer_above_level_zero(client, store, user_id):
    """PROMOTE_THRESHOLD is a flat 2 for every real level (1-4 alike) --
    genuinely needs a second consecutive correct answer to climb. Level 0
    is the only exception, and it's not threshold-based at all -- see
    test_submitting_an_intro_item_is_always_correct_and_promotes_straight_to_level_one."""
    fish_id = "banded-butterflyfish"
    assert PROMOTE_THRESHOLD == 2
    _set_progress(store, user_id, fish_id, level=2, streak_success=1, streak_fail=0)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=2)

    resp = client.post("/lesson/submit", json={"item_id": item_id, "answer": fish_id})
    data = resp.json()

    assert data["ok"] is True
    assert data["correct"] is True
    assert data["promoted"] is True
    assert data["new_level"] == 3
    assert data["streak"] == 0  # resets after promotion

    fish = _get_fish(store, user_id, fish_id)
    assert fish["level"] == 3
    assert fish["streak_success"] == 0


def test_demotion_on_second_consecutive_wrong_answer(client, store, user_id):
    fish_id = "banded-butterflyfish"
    wrong_answer_id = "french-angelfish"
    _set_progress(store, user_id, fish_id, level=2, streak_fail=1, streak_success=0, wrong_count=1)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=2)

    resp = client.post("/lesson/submit", json={"item_id": item_id, "answer": wrong_answer_id})
    data = resp.json()

    assert data["ok"] is True
    assert data["correct"] is False
    assert data["demoted"] is True
    assert data["new_level"] == 1
    assert data["matched_other"]["id"] == wrong_answer_id
    assert data["mnemonic"]  # wrong_count crossed 2 -> mnemonic revealed

    fish = _get_fish(store, user_id, fish_id)
    assert fish["level"] == 1
    assert fish["streak_fail"] == 0

    with store.engine.begin() as conn:
        weight = conn.execute(
            text("SELECT weight FROM confusion WHERE fish_id=:f AND other_id=:o"),
            {"f": fish_id, "o": wrong_answer_id},
        ).scalar()
    assert weight >= 1  # confusion weight bumped (or created) from the miss


def test_single_wrong_answer_does_not_demote_a_freshly_promoted_level_one_fish(client, store, user_id):
    """A single mistake should never immediately undo a fish's very first
    promotion (the guaranteed level 0->1 win from its intro card) -- it
    takes a genuine second consecutive wrong answer (DEMOTE_THRESHOLD=2) to
    drop it back down."""
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=1, streak_success=0, streak_fail=0)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=1)

    resp = client.post("/lesson/submit", json={"item_id": item_id, "answer": "not-a-real-species-id"})
    data = resp.json()

    assert data["ok"] is True
    assert data["correct"] is False
    assert data["demoted"] is False
    assert data["new_level"] == 1

    fish = _get_fish(store, user_id, fish_id)
    assert fish["level"] == 1
    assert fish["streak_fail"] == 1


def test_mastery_on_reaching_promote_threshold_at_level_four(client, store, user_id):
    """Level 4 uses the same flat PROMOTE_THRESHOLD=2 as every other real
    level -- two consecutive correct answers grants mastery instead of a
    fifth level."""
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=4, streak_success=PROMOTE_THRESHOLD - 1, mastered=0)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=4)

    resp = client.post("/lesson/submit", json={"item_id": item_id, "answer": "Banded Butterflyfish"})
    data = resp.json()

    assert data["ok"] is True
    assert data["correct"] is True
    assert data["mastered_now"] is True
    assert data["new_level"] == 4  # already at the ceiling, level itself doesn't change

    fish = _get_fish(store, user_id, fish_id)
    assert fish["mastered"] == 1
    assert fish["mastered_at"] > 0


def test_spelling_answer_within_edit_distance_tolerance_is_correct(client, store, user_id):
    fish_id = "banded-butterflyfish"  # normalized name > 8 chars -> tolerance 2
    _set_progress(store, user_id, fish_id, level=3)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=3)

    resp = client.post(
        "/lesson/submit", json={"item_id": item_id, "answer": "Banded Buterflyfish"}
    ).json()  # missing one 't' -> edit distance 1

    assert resp["correct"] is True
    assert resp["distance"] == 1


def test_spelling_answer_outside_tolerance_is_wrong_and_may_match_another_fish(client, store, user_id):
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=3)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=3)

    resp = client.post(
        "/lesson/submit", json={"item_id": item_id, "answer": "totally wrong species name"}
    ).json()

    assert resp["correct"] is False
    assert resp["distance"] > 2


# ---------- item lifecycle ----------


def test_resubmitting_an_already_done_item_returns_400(client):
    resp = client.post("/lesson/start")
    lesson_id = resp.json()["lesson_id"]
    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()

    first = client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": None})
    assert first.status_code == 200

    second = client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": None})
    assert second.status_code == 400
    assert second.json()["detail"] == "item not pending"


# ---------- full lesson flow ----------


def _answer_for(item):
    if item["question_type"] == "intro":
        return None
    return item["fish_id"]  # always the right choice for mc_easy/mc_hard


def test_full_lesson_completes_with_all_correct_answers(client, store):
    start = client.post("/lesson/start").json()
    lesson_id = start["lesson_id"]

    seen_items = 0
    summary = None
    for _ in range(200):  # generous cap; a fresh lesson has 15 items
        item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
        if item["done"]:
            summary = item.get("summary")
            break
        submit = client.post(
            "/lesson/submit", json={"item_id": item["item_id"], "answer": _answer_for(item)}
        ).json()
        assert submit["ok"] is True
        seen_items += 1

    assert summary is not None, "lesson never reported done"
    assert seen_items == start["planned_size"]  # every answer was correct -> no retry batch
    assert summary["wrong"] == 0
    # every non-retry item counts toward the tally now, intros included --
    # in an all-new lesson that's exactly one per new fish
    assert summary["correct"] == start["n_new"]
    assert summary["lessons_completed"] == 1

    lesson = _get_lesson(store, lesson_id)
    assert lesson["status"] == "completed"


def test_missed_item_gets_a_same_lesson_retry_that_does_not_affect_lesson_tally(client, store, user_id):
    """A fresh all-new lesson is now nothing but intro cards (which can
    never be "missed" -- they're always correct), so this rigs a genuine
    graded item directly rather than relying on one to show up naturally."""
    fish_id = "banded-butterflyfish"
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=1)

    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
    wrong_choice = next(c["id"] for c in item["choices"] if c["id"] != item["fish_id"])
    client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": wrong_choice})

    retry_item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
    assert retry_item["is_retry"] is True
    assert retry_item["fish_id"] == fish_id

    lesson_before = _get_lesson(store, lesson_id)
    resp = client.post(
        "/lesson/submit", json={"item_id": retry_item["item_id"], "answer": fish_id}
    ).json()
    lesson_after = _get_lesson(store, lesson_id)

    assert resp["ok"] is True
    assert resp["is_retry"] is True
    # retry submissions update lesson_items.status but never lessons.correct_count/wrong_count
    assert lesson_after["correct_count"] == lesson_before["correct_count"]
    assert lesson_after["wrong_count"] == lesson_before["wrong_count"]


# ---------- stats / browse ----------


def test_stats_reflect_completed_lesson_activity(client, store):
    with store.engine.begin() as conn:
        species_count = conn.execute(text("SELECT COUNT(*) FROM species")).scalar()

    before = client.get("/stats").json()
    assert before["lessons_completed"] == 0
    assert before["total"] == species_count

    start = client.post("/lesson/start").json()
    lesson_id = start["lesson_id"]
    for _ in range(200):
        item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
        if item["done"]:
            break
        client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": _answer_for(item)})

    after = client.get("/stats").json()
    assert after["lessons_completed"] == 1
    assert after["total_seen"] > before["total_seen"]
    assert sum(after["by_level"].values()) == species_count


def test_browse_returns_all_fish_with_photos(client, store):
    with store.engine.begin() as conn:
        species_count = conn.execute(text("SELECT COUNT(*) FROM species")).scalar()

    resp = client.get("/browse")
    data = resp.json()
    assert data["ok"] is True
    assert len(data["fish"]) == species_count
    for fish in data["fish"]:
        assert 1 <= len(fish["photos"]) <= 3
        assert fish["photos"][0]["file"]


# ---------- adding a species after users already exist ----------
#
# Covers the two halves of the add-new-species workflow (see the
# add-new-species skill): a species inserted into `species` after some
# users already exist must not silently break anything for either kind of
# user, before or after scripts/add_missing_species.py runs.


def _write_single_species_seed(tmp_path, fish_id):
    """A minimal seed.json + photo_manifest.json containing exactly one
    fish, so add_missing_species() can be exercised for real without
    dragging in the actual (large) seed_data/."""
    seed = {
        "fish": [
            {
                "id": fish_id,
                "name": "Test New Fish",
                "scientific_name": "Testus novus",
                "size": "1 in",
                "features": "distinctly fictional",
                "photo_file": f"{fish_id}.webp",
                "mnemonic": "made up for a test",
            }
        ],
        "confusion_pairs": [],
    }
    manifest = [
        {
            "id": fish_id,
            "name": "Test New Fish",
            "photos": [{"file": f"{fish_id}_1.webp", "credit": "Test", "web_file": f"{fish_id}_1.webp"}],
        }
    ]
    seed_path = tmp_path / "extra_seed.json"
    manifest_path = tmp_path / "extra_manifest.json"
    seed_path.write_text(json.dumps(seed))
    manifest_path.write_text(json.dumps(manifest))
    return seed_path, manifest_path


def test_brand_new_user_automatically_gets_a_species_added_after_other_users_signed_up(
    client, store, user_id
):
    """ensure_user() reads the live `species` table at signup time, so a
    species that shows up after other users already exist still reaches
    anyone who signs up afterward with zero extra migration step -- while
    `user_id` (already existing) stays untouched until one runs (next
    test)."""
    fish_id = "test-new-fish-a"
    with store.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO species (id, name, scientific_name, size, features, photo_file, mnemonic) "
                "VALUES (:id, 'Test New Fish', 'Testus novus', '1 in', 'fictional', :photo, 'test mnemonic')"
            ),
            {"id": fish_id, "photo": f"{fish_id}.webp"},
        )

    new_user_id = "test-user-brand-new"
    store.ensure_user(new_user_id)

    def has_progress(uid):
        with store.engine.begin() as conn:
            return (
                conn.execute(
                    text("SELECT COUNT(*) FROM progress WHERE user_id=:uid AND fish_id=:fid"),
                    {"uid": uid, "fid": fish_id},
                ).scalar()
                == 1
            )

    assert not has_progress(user_id)  # pre-existing user: untouched
    assert has_progress(new_user_id)  # freshly signed-up user: has it automatically

    # and it's actually quizzable for the new user, not just a bare DB row
    api = create_api(store)
    new_client = TestClient(api)
    new_client.cookies.set(COOKIE_NAME, new_user_id)

    _, item_id = _rig_lesson_item(store, new_user_id, fish_id, level_at_plan=0)
    data = new_client.post("/lesson/submit", json={"item_id": item_id, "answer": None}).json()
    assert data["ok"] is True
    assert data["promoted"] is True
    assert data["new_level"] == 1


def test_existing_user_gets_a_new_species_only_after_add_missing_species_runs(
    client, store, user_id, tmp_path
):
    """Mirrors an actual production event: a species gets added to
    seed_data/ after real users already exist, and
    scripts/add_missing_species.py backfills them. Runs the real migration
    function (not a reimplementation) against a temp store and a throwaway
    one-fish seed, so a regression in the actual migration path would be
    caught here."""
    fish_id = "test-new-fish-b"
    seed_path, manifest_path = _write_single_species_seed(tmp_path, fish_id)

    def has_progress():
        with store.engine.begin() as conn:
            return (
                conn.execute(
                    text("SELECT COUNT(*) FROM progress WHERE user_id=:uid AND fish_id=:fid"),
                    {"uid": user_id, "fid": fish_id},
                ).scalar()
                == 1
            )

    assert not has_progress()  # existing user predates this species

    result = add_missing_species(store=store, seed_path=seed_path, photo_manifest_path=manifest_path)
    assert result["added_species"] == [fish_id]
    assert result["backfilled_progress"] == 1  # just user_id, in this test's isolated store

    assert has_progress()  # now backfilled

    # and it's fully quizzable through the real API -- no exception for a
    # fish that didn't exist when this user first signed up
    _, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=0)
    data = client.post("/lesson/submit", json={"item_id": item_id, "answer": None}).json()
    assert data["ok"] is True
    assert data["promoted"] is True
    assert data["new_level"] == 1


# ---------- multi-user isolation ----------


def test_two_users_get_independent_progress(client, second_client, store, user_id, second_user_id):
    """Same store, two different cookie-identified users: answering
    questions as one must not move the other's level/streak at all."""
    fish_id = "banded-butterflyfish"

    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=1)
    _set_progress(store, user_id, fish_id, streak_success=1)
    client.post("/lesson/submit", json={"item_id": item_id, "answer": fish_id})

    user1_fish = _get_fish(store, user_id, fish_id)
    user2_fish = _get_fish(store, second_user_id, fish_id)

    assert user1_fish["level"] == 1  # promoted
    assert user2_fish["level"] == 0  # completely untouched
    assert user2_fish["streak_success"] == 0


def test_second_user_cannot_access_first_users_lesson(client, second_client):
    """A lesson_id is just an incrementing integer -- next_item/submit must
    check ownership, not just existence, or one user could read/answer
    another user's in-progress lesson by guessing ids."""
    start = client.post("/lesson/start").json()
    lesson_id = start["lesson_id"]

    resp = second_client.get("/lesson/next_item", params={"lesson_id": lesson_id})
    assert resp.status_code == 404

    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
    resp = second_client.post(
        "/lesson/submit", json={"item_id": item["item_id"], "answer": None}
    )
    assert resp.status_code == 400  # "item not pending" -- doesn't even reveal it belongs to someone else


def test_starting_a_lesson_does_not_abandon_another_users_active_lesson(client, second_client, store, user_id, second_user_id):
    first = client.post("/lesson/start").json()
    second_client.post("/lesson/start")  # a different user starting a lesson...

    lesson = _get_lesson(store, first["lesson_id"])
    assert lesson["status"] == "active"  # ...must not touch user_id's still-active lesson


# ---------- cross-device transfer links ----------


def _get_score(client):
    return client.get("/stats").json()["score"]


def test_transfer_link_returns_a_claim_path(client):
    resp = client.post("/account/transfer_link").json()
    assert resp["ok"] is True
    assert resp["path"].startswith("/claim/")


def test_transfer_preview_with_no_cookie_shows_null_current_score(client, store):
    token = client.post("/account/transfer_link").json()["path"].removeprefix("/claim/")

    bare_client = TestClient(create_api(store))  # deliberately no cookie set at all
    preview = bare_client.get("/account/transfer_preview", params={"t": token}).json()
    assert preview["ok"] is True
    assert preview["current_score"] is None
    assert preview["same_account"] is False


def test_transfer_preview_same_account_when_visiting_your_own_link(client):
    token = client.post("/account/transfer_link").json()["path"].removeprefix("/claim/")
    preview = client.get("/account/transfer_preview", params={"t": token}).json()
    assert preview["same_account"] is True
    assert preview["current_score"] == preview["incoming_score"]


def test_transfer_preview_shows_both_devices_distinct_scores_on_conflict(
    client, second_client, store, user_id, second_user_id
):
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=2)  # incoming: some progress
    # second_user_id (the "current device" in this scenario) stays at its default all-level-0 state

    token = client.post("/account/transfer_link").json()["path"].removeprefix("/claim/")
    preview = second_client.get("/account/transfer_preview", params={"t": token}).json()

    assert preview["same_account"] is False
    assert preview["current_score"] == _get_score(second_client)
    assert preview["incoming_score"] == _get_score(client)
    assert preview["incoming_score"] != preview["current_score"]


def test_transfer_confirm_replaces_the_devices_identity_with_the_incoming_account(
    client, second_client, store, user_id, second_user_id
):
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=3)  # give the source account some real, distinct progress
    incoming_score = _get_score(client)
    assert incoming_score != _get_score(second_client)  # sanity check they actually differ before confirming

    token = client.post("/account/transfer_link").json()["path"].removeprefix("/claim/")
    confirm = second_client.post("/account/transfer_confirm", json={"token": token})
    assert confirm.json()["ok"] is True

    # second_client's cookie now resolves to user_id's account/progress
    assert _get_score(second_client) == incoming_score


def test_transfer_preview_with_invalid_token_returns_400(client):
    resp = client.get("/account/transfer_preview", params={"t": "not-a-real-token"})
    assert resp.status_code == 400


def test_transfer_confirm_with_invalid_token_returns_400(client):
    resp = client.post("/account/transfer_confirm", json={"token": "not-a-real-token"})
    assert resp.status_code == 400
