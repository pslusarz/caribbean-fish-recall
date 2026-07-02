"""
Lesson-based spaced-repetition engine.

This is a direct port of the lustereczko `srs_lesson` custom tool (see
planning/ for migration notes). All business logic -- Levenshtein spelling
grading, confusion-weighted multiple-choice distractors, the
REVIEW_GAP/DECAY_GAP split, 70%-target lesson seeding, promoted/demoted/
mastered_now flags, rank score + history -- is unchanged from the prototype.
The only structural difference is that queries go through a SQLAlchemy
`Connection` (via `text()` with named params) instead of a raw sqlite3
cursor, and methods return plain dicts instead of `json.dumps(...)` strings
(FastAPI serializes dicts to JSON automatically).

Multi-user note: every public method takes a `user_id` and every query is
scoped by it. Species facts (name, scientific_name, mnemonic, ...) live in
the global `species` table; everything SRS-related (level, streaks, due
dates, mastery) lives in `progress`, keyed by (user_id, fish_id). `_get_fish`
below joins the two so the rest of this file can keep treating "fish" as one
merged row, exactly like the pre-multi-user version did.
"""

import random
import re
import time

from sqlalchemy import text

# Short-term scheduling: when a card resurfaces for lesson content (cram-friendly, minutes-to-hours)
REVIEW_GAP = {1: 15 * 60, 2: 45 * 60, 3: 2 * 3600, 4: 6 * 3600}
WRONG_REQUEUE = 10 * 60  # bring a missed card back soon regardless of level

# Long-term forgetting model: how long since last real review before we assume decay (day-scale)
DECAY_GAP = {1: 12 * 3600, 2: 24 * 3600, 3: 2 * 24 * 3600, 4: 3 * 24 * 3600}

PRIOR = {1: 0.90, 2: 0.65, 3: 0.50, 4: 0.35}

# Consecutive-correct/-wrong answers needed to climb/drop a level. Promotion
# threshold is eased at low levels (one correct answer takes a brand-new fish
# from 0->1, in the same lesson -- no more waiting on REVIEW_GAP[1] for a
# second touch) and tightened near mastery, so early progress feels almost
# immediate while the top of the ladder stays rigorous. Demotion threshold
# stays flat -- easing promotion at level 0 without also flattening demotion
# there would make a freshly-promoted level-1 fish droppable on a single
# mistake, undoing the "early positive feedback" this is meant to create.
PROMOTE_THRESHOLD = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3}
DEMOTE_THRESHOLD = 2

# Score contribution of each level, as a percent of one fish's max (100 at
# level 4). Front-loaded on purpose -- reaching level 1 already banks half
# of a fish's possible score, with diminishing gains after that -- so the
# overall score visibly jumps on a fish's first promotion instead of the old
# flat level/4 curve, which buried an early win under 57 still-untouched
# species. Overall score is just the average of this across all fish.
LEVEL_SCORE_WEIGHT = {0: 0, 1: 50, 2: 75, 3: 90, 4: 100}
LESSON_SIZE = 15
TARGET_RATE = 0.70

_FISH_SELECT = (
    "SELECT s.id AS id, s.name AS name, s.scientific_name AS scientific_name, "
    "s.size AS size, s.features AS features, s.mnemonic AS mnemonic, "
    "p.level AS level, p.streak_success AS streak_success, p.streak_fail AS streak_fail, "
    "p.seen_count AS seen_count, p.correct_count AS correct_count, p.wrong_count AS wrong_count, "
    "p.last_reviewed_at AS last_reviewed_at, p.next_due_at AS next_due_at, "
    "p.mastered AS mastered, p.mastered_at AS mastered_at, p.last_result AS last_result "
    "FROM species s JOIN progress p ON p.fish_id = s.id "
)


def normalize(s):
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def levenshtein(a, b):
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur_row = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur_row[j] = min(prev[j] + 1, cur_row[j - 1] + 1, prev[j - 1] + cost)
        prev = cur_row
    return prev[lb]


class SrsEngine:
    def __init__(self, store):
        self.store = store

    # ---------- helpers ----------

    def _get_fish(self, conn, user_id, fish_id):
        """Merged species+progress row for one fish, scoped to user_id. Looks
        and behaves exactly like the old single-user `fish` row."""
        row = conn.execute(
            text(_FISH_SELECT + "WHERE s.id = :fid AND p.user_id = :uid"),
            {"fid": fish_id, "uid": user_id},
        ).mappings().first()
        return row

    def _get_photos(self, conn, fish_id):
        rows = conn.execute(
            text("SELECT file, credit FROM photos WHERE fish_id=:fid ORDER BY seq ASC"),
            {"fid": fish_id},
        ).mappings().all()
        return [{"file": r["file"], "credit": r["credit"]} for r in rows]

    def _decay_pass(self, conn, user_id, now):
        rows = conn.execute(
            text(
                "SELECT fish_id, level, last_reviewed_at FROM progress "
                "WHERE user_id=:uid AND last_reviewed_at>0"
            ),
            {"uid": user_id},
        ).all()
        changed = 0
        for fid, level, last_reviewed_at in rows:
            if level <= 0:
                continue
            gap = DECAY_GAP.get(level, DECAY_GAP[4])
            if now > last_reviewed_at + gap:
                new_level = max(0, level - 1)
                conn.execute(
                    text(
                        "UPDATE progress SET level=:level, streak_success=0, streak_fail=0, "
                        "mastered=0, next_due_at=:now WHERE user_id=:uid AND fish_id=:fid"
                    ),
                    {"level": new_level, "now": now, "uid": user_id, "fid": fid},
                )
                changed += 1
        return changed

    def _compute_score(self, conn, user_id):
        weight_case = "CASE level " + " ".join(
            f"WHEN {lvl} THEN {w}" for lvl, w in LEVEL_SCORE_WEIGHT.items()
        ) + " ELSE 0 END"
        total = conn.execute(
            text(f"SELECT SUM({weight_case}) FROM progress WHERE user_id=:uid"), {"uid": user_id}
        ).scalar() or 0
        count = conn.execute(
            text("SELECT COUNT(*) FROM progress WHERE user_id=:uid"), {"uid": user_id}
        ).scalar()
        mastered = conn.execute(
            text("SELECT COUNT(*) FROM progress WHERE user_id=:uid AND mastered=1"),
            {"uid": user_id},
        ).scalar()
        score = round(1.0 * total / count, 1) if count else 0.0
        return score, mastered

    def _blended_accuracy(self, conn, user_id, level):
        row = conn.execute(
            text(
                "SELECT COUNT(*) AS n, SUM(li.correct) AS s FROM lesson_items li "
                "JOIN lessons l ON l.id = li.lesson_id "
                "WHERE l.user_id=:uid AND li.level_at_plan=:level AND li.is_retry=0 AND li.status='done'"
            ),
            {"uid": user_id, "level": level},
        ).mappings().first()
        n = row["n"] or 0
        s = row["s"] or 0
        pseudo_n = 5
        prior = PRIOR.get(level, 0.5)
        return (prior * pseudo_n + s) / (pseudo_n + n)

    def _log_rank(self, conn, user_id, now, reason):
        score, mastered = self._compute_score(conn, user_id)
        lessons_completed = conn.execute(
            text("SELECT lessons_completed FROM users WHERE id=:uid"), {"uid": user_id}
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO rank_history (user_id, ts, score, mastered_count, lessons_completed, reason) "
                "VALUES (:uid, :ts, :score, :mastered, :lessons_completed, :reason)"
            ),
            {
                "uid": user_id, "ts": now, "score": score, "mastered": mastered,
                "lessons_completed": lessons_completed, "reason": reason,
            },
        )
        return score, mastered

    def _build_question(self, conn, fish_row, level_at_plan):
        fish_id = fish_row["id"]
        if level_at_plan == 0:
            return {"question_type": "intro", "choices": None, "scaffold": None}
        qtype = {1: "mc_easy", 2: "mc_hard", 3: "spell_partial", 4: "spell_full"}[level_at_plan]
        choices = None
        scaffold = None
        if qtype in ("mc_easy", "mc_hard"):
            n_distractors = 3 if qtype == "mc_easy" else 6
            confused = conn.execute(
                text("SELECT other_id FROM confusion WHERE fish_id=:fid ORDER BY weight DESC"),
                {"fid": fish_id},
            ).all()
            distractor_ids = [r[0] for r in confused]
            random.shuffle(distractor_ids)
            distractor_ids = distractor_ids[:n_distractors]
            if len(distractor_ids) < n_distractors:
                exclude = [fish_id] + distractor_ids
                placeholders = ", ".join(f":ex{i}" for i in range(len(exclude)))
                params = {f"ex{i}": v for i, v in enumerate(exclude)}
                pool = conn.execute(
                    text(f"SELECT id FROM species WHERE id NOT IN ({placeholders})"),
                    params,
                ).all()
                pool_ids = [r[0] for r in pool]
                random.shuffle(pool_ids)
                distractor_ids += pool_ids[: n_distractors - len(distractor_ids)]
            if distractor_ids:
                placeholders = ", ".join(f":d{i}" for i in range(len(distractor_ids)))
                params = {f"d{i}": v for i, v in enumerate(distractor_ids)}
                opt_rows = conn.execute(
                    text(f"SELECT id, name FROM species WHERE id IN ({placeholders})"),
                    params,
                ).all()
            else:
                opt_rows = []
            choices = [{"id": fish_id, "name": fish_row["name"]}] + [
                {"id": r[0], "name": r[1]} for r in opt_rows
            ]
            random.shuffle(choices)
        elif qtype == "spell_partial":
            words = fish_row["name"].split(" ")
            out_words = []
            for w in words:
                if len(w) <= 1:
                    out_words.append(w)
                    continue
                chars = list(w)
                revealed = [chars[0]]
                for c in chars[1:]:
                    revealed.append("_" if c.isalpha() else c)
                out_words.append("".join(revealed))
            scaffold = " ".join(out_words)
        return {"question_type": qtype, "choices": choices, "scaffold": scaffold}

    # ---------- public actions ----------

    def start(self, user_id):
        now = time.time()
        with self.store.engine.begin() as conn:
            changed = self._decay_pass(conn, user_id, now)
            if changed:
                self._log_rank(conn, user_id, now, "decay")

            conn.execute(
                text("UPDATE lessons SET status='abandoned' WHERE status='active' AND user_id=:uid"),
                {"uid": user_id},
            )

            due_review = conn.execute(
                text(
                    "SELECT fish_id AS id, level, next_due_at FROM progress "
                    "WHERE user_id=:uid AND next_due_at>0 AND next_due_at<=:now ORDER BY next_due_at ASC"
                ),
                {"uid": user_id, "now": now},
            ).mappings().all()
            new_pool = conn.execute(
                text(
                    "SELECT fish_id AS id FROM progress "
                    "WHERE user_id=:uid AND level=0 AND next_due_at=0 ORDER BY fish_id"
                ),
                {"uid": user_id},
            ).mappings().all()

            fallback_used = False
            if not due_review and not new_pool:
                due_review = conn.execute(
                    text(
                        "SELECT fish_id AS id, level, next_due_at FROM progress WHERE user_id=:uid AND next_due_at>0 "
                        "ORDER BY level ASC, next_due_at ASC"
                    ),
                    {"uid": user_id},
                ).mappings().all()
                fallback_used = True

            acc_by_level = {lvl: self._blended_accuracy(conn, user_id, lvl) for lvl in (1, 2, 3, 4)}

            def acc_for(level):
                return 1.0 if level == 0 else acc_by_level.get(level, 0.5)

            if not due_review:
                f = 1.0
            elif not new_pool:
                f = 0.0
            else:
                p_review = sum(acc_for(r["level"]) for r in due_review) / len(due_review)
                f = 0.0 if p_review >= TARGET_RATE else (TARGET_RATE - p_review) / (1.0 - p_review)
                f = max(0.0, min(1.0, f))

            n_new = min(round(f * LESSON_SIZE), len(new_pool))
            n_review = min(LESSON_SIZE - n_new, len(due_review))
            if n_review < (LESSON_SIZE - n_new) and len(new_pool) > n_new:
                n_new = min(n_new + (LESSON_SIZE - n_new - n_review), len(new_pool))
            if n_new == 0 and new_pool and n_review < LESSON_SIZE:
                n_new = min(1, len(new_pool))
                n_review = min(LESSON_SIZE - n_new, len(due_review))

            selected_new = [r["id"] for r in new_pool[:n_new]]
            selected_review_rows = due_review[:n_review]
            selected_review = [r["id"] for r in selected_review_rows]
            review_levels = {r["id"]: r["level"] for r in selected_review_rows}

            random.shuffle(selected_new)
            random.shuffle(selected_review)

            tags = (["new"] * len(selected_new)) + (["review"] * len(selected_review))
            random.shuffle(tags)
            new_iter = iter(selected_new)
            review_iter = iter(selected_review)
            combined = []
            key_counter = 0
            for t in tags:
                if t == "new":
                    fid = next(new_iter)
                    combined.append({"fish_id": fid, "level_at_plan": 0, "is_reinforce": 0, "key": float(key_counter)})
                else:
                    fid = next(review_iter)
                    lvl = review_levels[fid]
                    combined.append({"fish_id": fid, "level_at_plan": lvl, "is_reinforce": 0, "key": float(key_counter)})
                key_counter += 1
            max_key = max(key_counter - 1, 0)
            for it in [x for x in combined if x["level_at_plan"] == 0]:
                offset = random.randint(4, 8)
                combined.append({
                    "fish_id": it["fish_id"], "level_at_plan": 1, "is_reinforce": 1,
                    "key": min(it["key"] + offset, max_key + 0.5)
                })
            combined.sort(key=lambda x: x["key"])

            for i in range(len(combined) - 1):
                a, b = combined[i]["fish_id"], combined[i + 1]["fish_id"]
                if a == b:
                    continue
                conf = conn.execute(
                    text("SELECT 1 FROM confusion WHERE fish_id=:a AND other_id=:b"),
                    {"a": a, "b": b},
                ).first()
                if conf and i + 2 < len(combined):
                    combined[i + 1], combined[i + 2] = combined[i + 2], combined[i + 1]

            new_lesson_id = conn.execute(
                text(
                    "INSERT INTO lessons (user_id, started_at, planned_size, target_rate, status) "
                    "VALUES (:uid, :now, :size, :rate, 'active') RETURNING id"
                ) if self.store.engine.dialect.name == "postgresql" else text(
                    "INSERT INTO lessons (user_id, started_at, planned_size, target_rate, status) "
                    "VALUES (:uid, :now, :size, :rate, 'active')"
                ),
                {"uid": user_id, "now": now, "size": len(combined), "rate": TARGET_RATE},
            )
            if self.store.engine.dialect.name == "postgresql":
                lesson_id = new_lesson_id.scalar()
            else:
                lesson_id = new_lesson_id.lastrowid

            for seq, it in enumerate(combined):
                conn.execute(
                    text(
                        "INSERT INTO lesson_items (lesson_id, seq, fish_id, level_at_plan, is_retry, is_reinforce, status) "
                        "VALUES (:lesson_id, :seq, :fish_id, :level_at_plan, 0, :is_reinforce, 'pending')"
                    ),
                    {
                        "lesson_id": lesson_id, "seq": seq, "fish_id": it["fish_id"],
                        "level_at_plan": it["level_at_plan"], "is_reinforce": it["is_reinforce"],
                    },
                )

            return {
                "ok": True, "lesson_id": lesson_id, "planned_size": len(combined),
                "n_new": len(selected_new), "n_review": len(selected_review), "target_rate": TARGET_RATE,
                "fallback_used": fallback_used,
            }

    def next_item(self, lesson_id, user_id):
        now = time.time()
        with self.store.engine.begin() as conn:
            lesson = conn.execute(
                text("SELECT * FROM lessons WHERE id=:lid AND user_id=:uid"),
                {"lid": lesson_id, "uid": user_id},
            ).mappings().first()
            if lesson is None:
                return {"ok": False, "error": "lesson not found"}

            row = conn.execute(
                text(
                    "SELECT * FROM lesson_items WHERE lesson_id=:lid AND status='pending' "
                    "ORDER BY seq ASC LIMIT 1"
                ),
                {"lid": lesson_id},
            ).mappings().first()

            if row is None:
                if lesson["status"] == "completed":
                    return {"ok": True, "done": True, "already_completed": True}

                retry_exists = conn.execute(
                    text("SELECT COUNT(*) FROM lesson_items WHERE lesson_id=:lid AND is_retry=1"),
                    {"lid": lesson_id},
                ).scalar()
                if not retry_exists:
                    misses = conn.execute(
                        text(
                            "SELECT fish_id, level_at_plan FROM lesson_items "
                            "WHERE lesson_id=:lid AND is_retry=0 AND status='done' AND correct=0"
                        ),
                        {"lid": lesson_id},
                    ).mappings().all()
                    if misses:
                        miss_list = [dict(m) for m in misses]
                        random.shuffle(miss_list)
                        max_seq = conn.execute(
                            text("SELECT COALESCE(MAX(seq),0) FROM lesson_items WHERE lesson_id=:lid"),
                            {"lid": lesson_id},
                        ).scalar()
                        for i, m in enumerate(miss_list):
                            conn.execute(
                                text(
                                    "INSERT INTO lesson_items (lesson_id, seq, fish_id, level_at_plan, is_retry, is_reinforce, status) "
                                    "VALUES (:lid, :seq, :fid, :lvl, 1, 0, 'pending')"
                                ),
                                {"lid": lesson_id, "seq": max_seq + 1 + i, "fid": m["fish_id"], "lvl": m["level_at_plan"]},
                            )
                        row = conn.execute(
                            text(
                                "SELECT * FROM lesson_items WHERE lesson_id=:lid AND status='pending' "
                                "ORDER BY seq ASC LIMIT 1"
                            ),
                            {"lid": lesson_id},
                        ).mappings().first()

            if row is None:
                score, mastered = self._log_rank(conn, user_id, now, "lesson")
                lessons_completed = conn.execute(
                    text("SELECT lessons_completed FROM users WHERE id=:uid"), {"uid": user_id}
                ).scalar() + 1
                conn.execute(
                    text("UPDATE users SET lessons_completed=:v WHERE id=:uid"),
                    {"v": lessons_completed, "uid": user_id},
                )
                conn.execute(
                    text("UPDATE lessons SET status='completed', completed_at=:now WHERE id=:lid"),
                    {"now": now, "lid": lesson_id},
                )
                lesson = conn.execute(
                    text("SELECT * FROM lessons WHERE id=:lid"), {"lid": lesson_id}
                ).mappings().first()
                return {
                    "ok": True, "done": True,
                    "summary": {
                        "correct": lesson["correct_count"], "wrong": lesson["wrong_count"],
                        "score": score, "mastered_count": mastered, "lessons_completed": lessons_completed,
                    },
                }

            fish = self._get_fish(conn, user_id, row["fish_id"])
            q = self._build_question(conn, fish, row["level_at_plan"])
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM lesson_items WHERE lesson_id=:lid AND status='pending'"),
                {"lid": lesson_id},
            ).scalar()
            hint = fish["mnemonic"] if (row["level_at_plan"] > 0 and fish["wrong_count"] >= 2) else ""
            photos = self._get_photos(conn, fish["id"])

            return {
                "ok": True, "done": False,
                "item_id": row["id"], "fish_id": fish["id"], "photos": photos,
                "name": fish["name"] if row["level_at_plan"] == 0 else None,
                "scientific_name": fish["scientific_name"] if row["level_at_plan"] == 0 else None,
                "size": fish["size"] if row["level_at_plan"] == 0 else None,
                "features": fish["features"] if row["level_at_plan"] == 0 else None,
                "mnemonic": fish["mnemonic"] if row["level_at_plan"] == 0 else None,
                "level_at_plan": row["level_at_plan"],
                "is_retry": bool(row["is_retry"]), "is_reinforce": bool(row["is_reinforce"]),
                "question_type": q["question_type"], "choices": q["choices"], "scaffold": q["scaffold"],
                "hint": hint, "remaining_in_lesson": remaining,
                "streak_success": fish["streak_success"], "streak_fail": fish["streak_fail"],
                "mastered": bool(fish["mastered"]),
                "promote_threshold": PROMOTE_THRESHOLD.get(fish["level"], DEMOTE_THRESHOLD),
            }

    def submit(self, item_id, answer, user_id):
        now = time.time()
        with self.store.engine.begin() as conn:
            item = conn.execute(
                text(
                    "SELECT li.* FROM lesson_items li JOIN lessons l ON l.id = li.lesson_id "
                    "WHERE li.id=:iid AND l.user_id=:uid"
                ),
                {"iid": item_id, "uid": user_id},
            ).mappings().first()
            if item is None or item["status"] != "pending":
                return {"ok": False, "error": "item not pending"}

            fish = self._get_fish(conn, user_id, item["fish_id"])
            level_at_plan = item["level_at_plan"]
            is_retry = bool(item["is_retry"])
            is_intro = (level_at_plan == 0)

            matched_other = None
            distance = None
            if is_intro:
                correct = True
            elif level_at_plan in (1, 2):
                correct = (answer == fish["id"])
                if not correct:
                    other = conn.execute(
                        text("SELECT id, name FROM species WHERE id=:aid"), {"aid": answer}
                    ).mappings().first()
                    if other:
                        matched_other = dict(other)
            else:
                norm_answer = normalize(answer or "")
                norm_correct = normalize(fish["name"])
                distance = levenshtein(norm_answer, norm_correct)
                tolerance = 1 if len(norm_correct) <= 8 else 2
                correct = distance <= tolerance
                if not correct:
                    others = conn.execute(
                        text("SELECT id, name FROM species WHERE id != :fid"), {"fid": fish["id"]}
                    ).all()
                    best, best_d = None, 999
                    for o in others:
                        d = levenshtein(norm_answer, normalize(o[1]))
                        if d < best_d:
                            best_d, best = d, {"id": o[0], "name": o[1]}
                    if best is not None and best_d <= 2 and best_d < distance:
                        matched_other = best
                        matched_other["distance"] = best_d

            conn.execute(
                text("UPDATE lesson_items SET status='done', correct=:c WHERE id=:iid"),
                {"c": 1 if correct else 0, "iid": item["id"]},
            )

            if not is_retry and not is_intro:
                conn.execute(
                    text(
                        "UPDATE lessons SET correct_count = correct_count + :c, "
                        "wrong_count = wrong_count + :w WHERE id=:lid"
                    ),
                    {"c": 1 if correct else 0, "w": 0 if correct else 1, "lid": item["lesson_id"]},
                )

            mnemonic_reveal = ""
            new_level = fish["level"]
            streak_out = fish["streak_success"]
            promoted = False
            demoted = False
            mastered_now = False

            if not is_retry:
                level = fish["level"]
                old_level = level
                old_mastered = fish["mastered"]
                streak_success = fish["streak_success"]
                streak_fail = fish["streak_fail"]
                mastered = fish["mastered"]
                seen_count = fish["seen_count"] + 1
                correct_count = fish["correct_count"]
                wrong_count = fish["wrong_count"]
                next_due_at = fish["next_due_at"]

                if is_intro:
                    pass
                elif correct:
                    correct_count += 1
                    streak_success += 1
                    streak_fail = 0
                    if streak_success >= PROMOTE_THRESHOLD.get(level, DEMOTE_THRESHOLD):
                        if level < 4:
                            level = min(level + 1, 4)
                        else:
                            mastered = 1
                        streak_success = 0
                    next_due_at = now + REVIEW_GAP.get(max(level, 1), REVIEW_GAP[4])
                else:
                    wrong_count += 1
                    streak_fail += 1
                    streak_success = 0
                    if fish["wrong_count"] + 1 >= 2:
                        mnemonic_reveal = fish["mnemonic"]
                    if streak_fail >= DEMOTE_THRESHOLD:
                        level = max(0, level - 1)
                        streak_fail = 0
                        mastered = 0
                    next_due_at = now + WRONG_REQUEUE
                    if matched_other is not None:
                        conn.execute(
                            text(
                                "INSERT INTO confusion (fish_id, other_id, weight) VALUES (:fid, :oid, 1) "
                                "ON CONFLICT (fish_id, other_id) DO UPDATE SET weight = confusion.weight + 1"
                            ),
                            {"fid": fish["id"], "oid": matched_other["id"]},
                        )

                promoted = (not is_intro) and (level > old_level)
                demoted = (not is_intro) and (level < old_level)
                mastered_now = (not is_intro) and (mastered == 1 and old_mastered == 0)

                mastered_at = fish["mastered_at"]
                if mastered_now:
                    mastered_at = now
                elif old_mastered == 1 and mastered == 0:
                    mastered_at = 0  # demoted out of mastery -- clear the timestamp too

                conn.execute(
                    text(
                        "UPDATE progress SET level=:level, streak_success=:ss, streak_fail=:sf, "
                        "seen_count=:seen, correct_count=:cc, wrong_count=:wc, "
                        "last_reviewed_at=:now, next_due_at=:due, mastered=:mastered, "
                        "mastered_at=:mastered_at, last_result=:result "
                        "WHERE user_id=:uid AND fish_id=:fid"
                    ),
                    {
                        "level": level, "ss": streak_success, "sf": streak_fail, "seen": seen_count,
                        "cc": correct_count, "wc": wrong_count, "now": now, "due": next_due_at,
                        "mastered": mastered, "mastered_at": mastered_at,
                        "result": "correct" if correct else "wrong", "uid": user_id, "fid": fish["id"],
                    },
                )
                new_level = level
                streak_out = streak_success

            remaining = conn.execute(
                text("SELECT COUNT(*) FROM lesson_items WHERE lesson_id=:lid AND status='pending'"),
                {"lid": item["lesson_id"]},
            ).scalar()

            return {
                "ok": True, "correct": correct, "is_retry": is_retry, "is_intro": is_intro,
                "correct_name": fish["name"], "scientific_name": fish["scientific_name"], "features": fish["features"],
                "matched_other": matched_other, "distance": distance, "mnemonic": mnemonic_reveal,
                "new_level": new_level, "streak": streak_out, "remaining_in_lesson": remaining,
                "promoted": promoted, "demoted": demoted, "mastered_now": mastered_now,
            }

    def stats(self, user_id):
        now = time.time()
        with self.store.engine.begin() as conn:
            changed = self._decay_pass(conn, user_id, now)
            if changed:
                self._log_rank(conn, user_id, now, "decay")

            score, mastered = self._compute_score(conn, user_id)
            total = conn.execute(
                text("SELECT COUNT(*) FROM progress WHERE user_id=:uid"), {"uid": user_id}
            ).scalar()
            lessons_completed = conn.execute(
                text("SELECT lessons_completed FROM users WHERE id=:uid"), {"uid": user_id}
            ).scalar()
            by_level = conn.execute(
                text("SELECT level, COUNT(*) AS c FROM progress WHERE user_id=:uid GROUP BY level"),
                {"uid": user_id},
            ).mappings().all()
            totals = conn.execute(
                text(
                    "SELECT SUM(seen_count) AS s, SUM(correct_count) AS c, SUM(wrong_count) AS w "
                    "FROM progress WHERE user_id=:uid"
                ),
                {"uid": user_id},
            ).mappings().first()
            hardest = conn.execute(
                text(
                    "SELECT s.name AS name, p.wrong_count AS wrong_count, p.correct_count AS correct_count "
                    "FROM progress p JOIN species s ON s.id = p.fish_id "
                    "WHERE p.user_id=:uid AND p.wrong_count > 0 "
                    "ORDER BY p.wrong_count DESC LIMIT 5"
                ),
                {"uid": user_id},
            ).mappings().all()
            # confusion is intentionally global (shared signal across all
            # users), not scoped to user_id -- see srs_store.py docstring.
            top_confusions = conn.execute(
                text(
                    "SELECT f.name AS a, o.name AS b, c.weight AS weight FROM confusion c "
                    "JOIN species f ON f.id = c.fish_id JOIN species o ON o.id = c.other_id "
                    "WHERE c.weight > 2 ORDER BY c.weight DESC LIMIT 5"
                )
            ).mappings().all()
            history = conn.execute(
                text(
                    "SELECT ts, score, mastered_count, reason FROM rank_history "
                    "WHERE user_id=:uid ORDER BY ts ASC"
                ),
                {"uid": user_id},
            ).mappings().all()
            active_lesson = conn.execute(
                text(
                    "SELECT id FROM lessons WHERE user_id=:uid AND status='active' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": user_id},
            ).mappings().first()

            return {
                "ok": True, "total": total, "score": score, "mastered": mastered,
                "lessons_completed": lessons_completed,
                "by_level": {str(r["level"]): r["c"] for r in by_level},
                "total_seen": totals["s"] or 0, "total_correct": totals["c"] or 0, "total_wrong": totals["w"] or 0,
                "hardest": [dict(r) for r in hardest],
                "top_confusions": [dict(r) for r in top_confusions],
                "history": [{"ts": r["ts"], "score": r["score"]} for r in history],
                "active_lesson_id": active_lesson["id"] if active_lesson else None,
            }

    def browse(self, user_id):
        with self.store.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.id AS id, s.name AS name, s.scientific_name AS scientific_name, "
                    "s.size AS size, s.features AS features, s.mnemonic AS mnemonic, "
                    "p.level AS level, p.mastered AS mastered "
                    "FROM species s JOIN progress p ON p.fish_id = s.id AND p.user_id = :uid "
                    "ORDER BY s.name"
                ),
                {"uid": user_id},
            ).mappings().all()
            fish_list = []
            for r in rows:
                d = dict(r)
                d["photos"] = self._get_photos(conn, r["id"])
                fish_list.append(d)
            return {"ok": True, "fish": fish_list}
