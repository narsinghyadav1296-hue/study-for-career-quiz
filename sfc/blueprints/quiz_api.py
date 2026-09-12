"""
Quiz-taking API. Logic is copied unchanged from the original app.py -
timer/late-submission handling, no-double-scoring, rate limiting,
privacy-friendly analytics. The only change is that `load_quiz()` now
transparently also understands DB-backed quizzes (see quiz_service.py);
everything below this line is exactly as before.
"""
import time
import uuid
from collections import defaultdict

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db
from ..quiz_service import load_quiz, public_quiz_payload, result_message

quiz_api_bp = Blueprint("quiz_api", __name__)

# Very small in-memory rate limiter (per IP) - good enough for a single
# process deployment. Resets are time-window based, no external deps.
_rate_buckets = defaultdict(list)


def rate_limited(key):
    now = time.time()
    window = current_app.config["RATE_LIMIT_WINDOW"]
    max_requests = current_app.config["RATE_LIMIT_MAX_REQUESTS"]
    bucket = _rate_buckets[key]
    while bucket and bucket[0] < now - window:
        bucket.pop(0)
    if len(bucket) >= max_requests:
        return True
    bucket.append(now)
    return False


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


@quiz_api_bp.route("/api/quiz/<int:quiz_id>", methods=["GET"])
def api_get_quiz(quiz_id):
    if rate_limited(f"get:{client_ip()}"):
        return jsonify({"error": "Too many requests. Please slow down."}), 429

    quiz = load_quiz(quiz_id)
    if quiz is None:
        return jsonify({"error": "Quiz not found"}), 404

    session_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO sessions (session_id, quiz_id, started_at, submitted, client_ip) "
        "VALUES (?, ?, ?, 0, ?)",
        (session_id, quiz_id, time.time(), client_ip()),
    )
    db.commit()

    payload = public_quiz_payload(quiz)
    payload["session_id"] = session_id
    return jsonify(payload)


@quiz_api_bp.route("/api/quiz/<int:quiz_id>/submit", methods=["POST"])
def api_submit_quiz(quiz_id):
    if rate_limited(f"submit:{client_ip()}"):
        return jsonify({"error": "Too many requests. Please slow down."}), 429

    quiz = load_quiz(quiz_id)
    if quiz is None:
        return jsonify({"error": "Quiz not found"}), 404

    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    answers = body.get("answers", {})  # {question_id(str): selected_index}

    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    db = get_db()
    row = db.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND quiz_id = ?",
        (session_id, quiz_id),
    ).fetchone()

    if row is None:
        return jsonify({"error": "Invalid or expired session"}), 400

    if row["submitted"]:
        total = len(quiz["questions"])
        return jsonify(
            {
                "score": row["score"],
                "total": total,
                "max_marks": quiz.get("max_marks", total),
                "message": result_message(row["score"], quiz.get("max_marks", total)),
                "already_submitted": True,
            }
        )

    elapsed = time.time() - row["started_at"]
    allowed = quiz["duration_seconds"] + current_app.config["SUBMIT_GRACE_SECONDS"]
    late = elapsed > allowed

    total = len(quiz["questions"])
    score = 0.0
    correct_count = wrong_count = unattempted_count = 0
    marks_correct = quiz.get("marks_correct", 1)
    marks_wrong = quiz.get("marks_wrong", 0)
    marks_unattempted = quiz.get("marks_unattempted", 0)
    per_question = []
    for q in quiz["questions"]:
        qid = str(q["id"])
        selected = answers.get(qid)
        is_correct = (not late) and selected == q["correct_answer"]
        if selected is None:
            unattempted_count += 1
            if not late:
                score += marks_unattempted
        elif is_correct:
            correct_count += 1
            score += marks_correct
        else:
            wrong_count += 1
            score += marks_wrong
        per_question.append(
            {
                "id": q["id"],
                "correct_answer": q["correct_answer"],
                "selected": selected,
                "is_correct": is_correct,
            }
        )

    db.execute(
        "UPDATE sessions SET submitted = 1, score = ? WHERE session_id = ?",
        (score, session_id),
    )
    db.commit()

    return jsonify(
        {
            "score": round(score, 2),
            "total": total,
            "max_marks": quiz.get("max_marks", total),
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "unattempted_count": unattempted_count,
            "late": late,
            "message": result_message(score, quiz.get("max_marks", total)),
            "per_question": per_question,
        }
    )


@quiz_api_bp.route("/api/analytics", methods=["POST"])
def api_analytics():
    if rate_limited(f"analytics:{client_ip()}"):
        return jsonify({"error": "Too many requests"}), 429

    body = request.get_json(silent=True) or {}
    event_name = body.get("event")
    quiz_id = body.get("quiz_id")
    score_range = body.get("score_range")

    allowed_events = {
        "quiz_started",
        "quiz_completed",
        "telegram_button_clicked",
    }
    if event_name not in allowed_events:
        return jsonify({"error": "Unknown event"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO analytics_events (event_name, quiz_id, score_range, created_at) "
        "VALUES (?, ?, ?, ?)",
        (event_name, quiz_id, score_range, time.time()),
    )
    db.commit()
    return jsonify({"ok": True})


@quiz_api_bp.route("/api/analytics/summary", methods=["GET"])
def api_analytics_summary():
    db = get_db()
    rows = db.execute(
        "SELECT event_name, score_range, COUNT(*) as cnt FROM analytics_events "
        "GROUP BY event_name, score_range"
    ).fetchall()
    return jsonify([dict(r) for r in rows])
