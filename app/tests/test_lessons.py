"""
Lesson-flow tests for the FastAPI API, run against a temporary SQLite
database (see conftest.py) -- never against data/srs.db or data/srs2.db.

These exercise the multi-user engine (cookie-assigned user_id, species vs.
progress split) as it exists today, so they double as a safety net for
future changes: if these still pass, per-user behavior and isolation were
preserved.
"""

from sqlalchemy import text


def _rig_lesson_item(store, user_id, fish_id, level_at_plan, is_retry=0, is_reinforce=0):
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
                "INSERT INTO lesson_items (lesson_id, seq, fish_id, level_at_plan, is_retry, is_reinforce, status) "
                "VALUES (:lid, 0, :fid, :lvl, :retry, :reinforce, 'pending')"
            ),
            {"lid": lesson_id, "fid": fish_id, "lvl": level_at_plan, "retry": is_retry, "reinforce": is_reinforce},
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
                    "p.seen_count AS seen_count, p.mastered AS mastered, p.mastered_at AS mastered_at "
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
    all-new lesson: 15 new items plus one same-lesson reinforce clone per
    new item (level_at_plan=1) inserted 4-8 slots later."""
    resp = client.post("/lesson/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["n_new"] == 15
    assert data["n_review"] == 0
    assert data["planned_size"] == 30  # 15 new + 15 reinforce clones
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


def test_submitting_an_intro_item_is_always_correct_and_does_not_change_level(client, store, user_id):
    resp = client.post("/lesson/start")
    lesson_id = resp.json()["lesson_id"]
    item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()

    before = _get_fish(store, user_id, item["fish_id"])
    submit = client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": None}).json()

    assert submit["ok"] is True
    assert submit["is_intro"] is True
    assert submit["correct"] is True
    assert submit["promoted"] is False
    assert submit["demoted"] is False
    assert submit["new_level"] == 0

    after = _get_fish(store, user_id, item["fish_id"])
    assert after["level"] == before["level"] == 0
    assert after["seen_count"] == before["seen_count"] + 1


def test_reinforce_item_is_mc_easy_with_the_correct_fish_among_choices(client):
    resp = client.post("/lesson/start")
    lesson_id = resp.json()["lesson_id"]

    reinforce_item = None
    for _ in range(30):
        item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
        if item["done"]:
            break
        if item["is_reinforce"]:
            reinforce_item = item
            break
        # answer whatever we're given so the lesson keeps advancing
        if item["question_type"] == "intro":
            answer = None
        else:
            answer = item["fish_id"]
        client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": answer})

    assert reinforce_item is not None, "expected a reinforce item within the first lesson"
    assert reinforce_item["question_type"] == "mc_easy"
    assert reinforce_item["level_at_plan"] == 1
    choice_ids = [c["id"] for c in reinforce_item["choices"]]
    assert reinforce_item["fish_id"] in choice_ids
    assert len(reinforce_item["choices"]) == 4  # correct + 3 distractors


# ---------- promotion / demotion / mastery ----------


def test_promotion_on_second_consecutive_correct_answer(client, store, user_id):
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=0, streak_success=1, streak_fail=0)
    lesson_id, item_id = _rig_lesson_item(store, user_id, fish_id, level_at_plan=1)

    resp = client.post("/lesson/submit", json={"item_id": item_id, "answer": fish_id})
    data = resp.json()

    assert data["ok"] is True
    assert data["correct"] is True
    assert data["promoted"] is True
    assert data["new_level"] == 1
    assert data["streak"] == 0  # resets after promotion

    fish = _get_fish(store, user_id, fish_id)
    assert fish["level"] == 1
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


def test_mastery_on_second_consecutive_correct_at_level_four(client, store, user_id):
    fish_id = "banded-butterflyfish"
    _set_progress(store, user_id, fish_id, level=4, streak_success=1, mastered=0)
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
    for _ in range(200):  # generous cap; a fresh lesson has 30 items
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
    # only non-intro, non-retry items count toward the tally -- in an
    # all-new lesson that's exactly the one reinforce clone per new fish
    assert summary["correct"] == start["n_new"]
    assert summary["lessons_completed"] == 1

    lesson = _get_lesson(store, lesson_id)
    assert lesson["status"] == "completed"


def test_missed_item_gets_a_same_lesson_retry_that_does_not_affect_lesson_tally(client, store):
    start = client.post("/lesson/start").json()
    lesson_id = start["lesson_id"]

    missed_fish_id = None
    retry_item = None
    for _ in range(200):
        item = client.get("/lesson/next_item", params={"lesson_id": lesson_id}).json()
        if item["done"]:
            break
        if item["is_retry"]:
            retry_item = item
            break
        if missed_fish_id is None and item["question_type"] == "mc_easy":
            # deliberately answer the first mc_easy item wrong
            wrong_choice = next(c["id"] for c in item["choices"] if c["id"] != item["fish_id"])
            client.post("/lesson/submit", json={"item_id": item["item_id"], "answer": wrong_choice})
            missed_fish_id = item["fish_id"]
        else:
            client.post(
                "/lesson/submit", json={"item_id": item["item_id"], "answer": _answer_for(item)}
            )

    assert missed_fish_id is not None
    assert retry_item is not None
    assert retry_item["fish_id"] == missed_fish_id
    assert retry_item["is_retry"] is True

    lesson_before = _get_lesson(store, lesson_id)
    resp = client.post(
        "/lesson/submit", json={"item_id": retry_item["item_id"], "answer": missed_fish_id}
    ).json()
    lesson_after = _get_lesson(store, lesson_id)

    assert resp["ok"] is True
    assert resp["is_retry"] is True
    # retry submissions update lesson_items.status but never lessons.correct_count/wrong_count
    assert lesson_after["correct_count"] == lesson_before["correct_count"]
    assert lesson_after["wrong_count"] == lesson_before["wrong_count"]


# ---------- stats / browse ----------


def test_stats_reflect_completed_lesson_activity(client, store):
    before = client.get("/stats").json()
    assert before["lessons_completed"] == 0
    assert before["total"] == 58

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
    assert sum(after["by_level"].values()) == 58


def test_browse_returns_all_fish_with_photos(client):
    resp = client.get("/browse")
    data = resp.json()
    assert data["ok"] is True
    assert len(data["fish"]) == 58
    for fish in data["fish"]:
        assert 1 <= len(fish["photos"]) <= 3
        assert fish["photos"][0]["file"]


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
