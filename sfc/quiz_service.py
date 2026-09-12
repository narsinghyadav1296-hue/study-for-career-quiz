"""
Quiz service - the single place that knows how to load a quiz "by public
id" no matter where it actually lives.

    Existing Quiz 1-4 (and any future hand-edited JSON file):
        data/quizzes/quiz_<id>.json  --(unchanged loader/cache)-->  dict

    Admin-created / DOCX-imported quizzes:
        SQLite `quizzes` + `quiz_questions` tables  -->  same-shaped dict

Every quiz - regardless of source - is normalised into the *exact* dict
shape the original app.py already used:

    {
        "quiz_id": int,
        "title": str, "subtitle": str, "description": str,
        "duration_seconds": int,
        "marks_correct": float, "marks_wrong": float, "marks_unattempted": float,
        "max_marks": float,
        "questions": [
            {"id": int, "question": str, "options": [4 strings],
             "correct_answer": int, "explanation": str}
        ],
    }

This means /quiz/<id>, /api/quiz/<id>, /api/quiz/<id>/submit and the
scoring/timer/late-submission logic in the API blueprint do not need to
know or care whether a quiz came from a JSON file or the database -
exactly the "existing loader / new DB source, same quiz-taking UI"
architecture requested.

Public ids for DB-backed quizzes are offset (see config.DB_QUIZ_ID_OFFSET,
default 1000) so they can never collide with the existing JSON ids 1-4.
"""
import json
from flask import current_app

from .db import get_db

# Cache only the JSON-file quizzes (their content is static on disk, same
# behaviour as the original app.py). DB-backed quizzes are intentionally
# never cached so admin edits/publishes show up immediately.
_json_quiz_cache = {}


def _load_json_quiz(quiz_id):
    if quiz_id in _json_quiz_cache:
        return _json_quiz_cache[quiz_id]
    path = current_app.config["QUIZ_DIR"] / f"quiz_{quiz_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _json_quiz_cache[quiz_id] = data
    return data


def _load_db_quiz(internal_id, include_unpublished=False):
    db = get_db()
    row = db.execute(
        "SELECT * FROM quizzes WHERE id = ? AND deleted_at IS NULL", (internal_id,)
    ).fetchone()
    if row is None:
        return None
    if not include_unpublished and row["status"] != "published":
        return None

    question_rows = db.execute(
        "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY position ASC",
        (internal_id,),
    ).fetchall()

    questions = []
    for q in question_rows:
        questions.append(
            {
                "id": q["position"],
                "question": q["question"],
                "options": [q["option_a"], q["option_b"], q["option_c"], q["option_d"]],
                "correct_answer": q["correct_answer"],
                "explanation": q["explanation"] or "",
            }
        )

    total = len(questions)
    marks_correct = row["marks_correct"]
    max_marks = total * marks_correct

    offset = current_app.config["DB_QUIZ_ID_OFFSET"]
    return {
        "quiz_id": internal_id + offset,
        "title": row["title"],
        "subtitle": row["subtitle"] or "",
        "description": row["description"] or "",
        "duration_seconds": row["duration_seconds"],
        "marks_correct": marks_correct,
        "marks_wrong": row["marks_wrong"],
        "marks_unattempted": row["marks_unattempted"],
        "max_marks": max_marks,
        "questions": questions,
    }


def load_quiz(quiz_id, include_unpublished=False):
    """Main entry point used by every public/API route. Behaviour for
    quiz_id 1-4 (and any other JSON-file id) is byte-for-byte identical to
    the original app.py."""
    offset = current_app.config["DB_QUIZ_ID_OFFSET"]
    if quiz_id < offset:
        return _load_json_quiz(quiz_id)
    return _load_db_quiz(quiz_id - offset, include_unpublished=include_unpublished)


def public_quiz_payload(quiz):
    """Strip correct_answer / explanation before sending to the client.
    Identical to the original app.py."""
    questions = [
        {"id": q["id"], "question": q["question"], "options": q["options"]}
        for q in quiz["questions"]
    ]
    return {
        "quiz_id": quiz["quiz_id"],
        "title": quiz["title"],
        "subtitle": quiz["subtitle"],
        "description": quiz.get("description", ""),
        "duration_seconds": quiz["duration_seconds"],
        "total_questions": len(questions),
        "questions": questions,
    }


def list_available_quizzes():
    """Scan JSON files (existing behaviour, unchanged) + published DB
    quizzes, merge, and return the same light-metadata shape the original
    app.py produced for listing pages."""
    quizzes = []

    quiz_dir = current_app.config["QUIZ_DIR"]
    for path in sorted(quiz_dir.glob("quiz_*.json")):
        try:
            quiz_id = int(path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        quiz = _load_json_quiz(quiz_id)
        if not quiz:
            continue
        quizzes.append(_summarize(quiz))

    offset = current_app.config["DB_QUIZ_ID_OFFSET"]
    db = get_db()
    rows = db.execute(
        "SELECT id FROM quizzes WHERE status = 'published' AND deleted_at IS NULL"
    ).fetchall()
    for row in rows:
        quiz = _load_db_quiz(row["id"])
        if quiz:
            quizzes.append(_summarize(quiz))

    quizzes.sort(key=lambda q: q["quiz_id"])
    return quizzes


def _summarize(quiz):
    total_questions = len(quiz.get("questions", []))
    duration_seconds = quiz.get("duration_seconds", 0)
    return {
        "quiz_id": quiz["quiz_id"],
        "title": quiz["title"],
        "subtitle": quiz.get("subtitle", ""),
        "description": quiz.get("description", ""),
        "duration_seconds": duration_seconds,
        "duration_minutes": round(duration_seconds / 60) if duration_seconds else 0,
        "total_questions": total_questions,
        "max_marks": quiz.get("max_marks", total_questions),
        "has_negative_marking": bool(quiz.get("marks_wrong")),
    }


def score_range_bucket(score, total):
    pct = (score / total * 100) if total else 0
    if pct >= 80:
        return "80-100"
    if pct >= 60:
        return "60-79"
    return "0-59"


def result_message(score, total):
    bucket = score_range_bucket(score, total)
    if bucket == "80-100":
        return {
            "headline": "\U0001F389 EXCELLENT PERFORMANCE!",
            "eligible": "\U0001F7E2 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
        }
    if bucket == "60-79":
        return {
            "headline": "\U0001F3AF GOOD PERFORMANCE!",
            "eligible": "\U0001F7E2 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
        }
    return {
        "headline": "\U0001F4AA KEEP GOING!",
        "eligible": "\U0001F7E2 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
    }
