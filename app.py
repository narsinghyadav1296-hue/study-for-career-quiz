"""
Study For Career - Telegram Quiz Web App
Flask backend: serves quiz pages, validates answers server-side,
tracks lightweight privacy-friendly analytics, and prevents client-side
answer/score tampering.
"""
import json
import os
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, g, abort

BASE_DIR = Path(__file__).resolve().parent
QUIZ_DIR = BASE_DIR / "data" / "quizzes"
DB_PATH = BASE_DIR / "quiz_app.db"

TELEGRAM_CHANNEL_URL = "https://t.me/studyforcareer_msn"
TELEGRAM_CHANNEL_USERNAME = "@studyforcareer_msn"
TELEGRAM_CHANNEL_NAME = "Study For Career"

# Grace period (seconds) added on top of quiz duration to account for
# network latency between the timer hitting 0 on the client and the
# submit request arriving at the server.
SUBMIT_GRACE_SECONDS = 15

app = Flask(__name__)

# --------------------------------------------------------------------------
# Very small in-memory rate limiter (per IP) - good enough for a single
# process deployment. Resets are time-window based, no external deps.
# --------------------------------------------------------------------------
_rate_buckets = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 30  # requests per window per IP per route-group


def rate_limited(key):
    now = time.time()
    bucket = _rate_buckets[key]
    # drop old entries
    while bucket and bucket[0] < now - RATE_LIMIT_WINDOW:
        bucket.pop(0)
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    bucket.append(now)
    return False


def client_ip():
    # Works behind simple reverse proxies too (nginx sets X-Forwarded-For)
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            quiz_id INTEGER NOT NULL,
            started_at REAL NOT NULL,
            submitted INTEGER NOT NULL DEFAULT 0,
            score INTEGER,
            client_ip TEXT
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            quiz_id INTEGER,
            score_range TEXT,
            created_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Quiz data loading (data-driven; each quiz lives in its own JSON file)
# --------------------------------------------------------------------------
_quiz_cache = {}


def load_quiz(quiz_id):
    if quiz_id in _quiz_cache:
        return _quiz_cache[quiz_id]
    path = QUIZ_DIR / f"quiz_{quiz_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _quiz_cache[quiz_id] = data
    return data


def public_quiz_payload(quiz):
    """Strip correct_answer / explanation before sending to the client."""
    questions = [
        {
            "id": q["id"],
            "question": q["question"],
            "options": q["options"],
        }
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
    """Scan data/quizzes for quiz_<id>.json files and return light metadata
    for listing pages. Never includes correct_answer / explanation."""
    quizzes = []
    for path in sorted(QUIZ_DIR.glob("quiz_*.json")):
        try:
            quiz_id = int(path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        quiz = load_quiz(quiz_id)
        if not quiz:
            continue
        total_questions = len(quiz.get("questions", []))
        duration_seconds = quiz.get("duration_seconds", 0)
        quizzes.append(
            {
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
        )
    quizzes.sort(key=lambda q: q["quiz_id"])
    return quizzes


# --------------------------------------------------------------------------
# Static reference data for the informational pages (Phase 1).
# No student data, no fabricated counts/statistics - descriptive only.
# --------------------------------------------------------------------------
EXAMS = [
    {
        "slug": "uppcs",
        "name": "UPPCS",
        "full_name": "Uttar Pradesh Provincial Civil Services",
        "short_description": (
            "उत्तर प्रदेश लोक सेवा आयोग (UPPSC) द्वारा आयोजित राज्य सिविल सेवा परीक्षा, "
            "जिसमें प्रारंभिक परीक्षा, मुख्य परीक्षा एवं साक्षात्कार सम्मिलित हैं।"
        ),
    },
    {
        "slug": "upsc",
        "name": "UPSC",
        "full_name": "Union Public Service Commission (Civil Services Examination)",
        "short_description": (
            "भारत सरकार की सर्वोच्च सिविल सेवा परीक्षा — IAS, IPS, IFS सहित केंद्रीय "
            "सेवाओं के लिए तीन चरणों (प्रारंभिक, मुख्य, साक्षात्कार) में आयोजित होती है।"
        ),
    },
    {
        "slug": "ro-aro",
        "name": "RO/ARO",
        "full_name": "Review Officer / Assistant Review Officer",
        "short_description": (
            "उत्तर प्रदेश लोक सेवा आयोग द्वारा आयोजित समीक्षा अधिकारी / सहायक समीक्षा "
            "अधिकारी पदों हेतु राज्य स्तरीय परीक्षा।"
        ),
    },
    {
        "slug": "ssc",
        "name": "SSC",
        "full_name": "Staff Selection Commission",
        "short_description": (
            "केंद्र सरकार के विभिन्न मंत्रालयों एवं विभागों में ग्रुप B व ग्रुप C पदों "
            "हेतु आयोजित होने वाली राष्ट्रीय स्तरीय परीक्षा।"
        ),
    },
    {
        "slug": "railway",
        "name": "Railway",
        "full_name": "Railway Recruitment Board (RRB) Examinations",
        "short_description": (
            "भारतीय रेलवे में विभिन्न तकनीकी एवं गैर-तकनीकी पदों हेतु रेलवे भर्ती बोर्ड "
            "द्वारा आयोजित परीक्षाएँ।"
        ),
    },
    {
        "slug": "teaching",
        "name": "Teaching Exams",
        "full_name": "Teaching Eligibility Examinations (TET / CTET / Super TET)",
        "short_description": (
            "प्राथमिक एवं उच्च प्राथमिक स्तर पर शिक्षक भर्ती हेतु आयोजित होने वाली "
            "शिक्षक पात्रता परीक्षाएँ।"
        ),
    },
]
EXAMS_BY_SLUG = {e["slug"]: e for e in EXAMS}

STUDY_MATERIAL_CATEGORIES = [
    "History", "Polity", "Geography", "Economy",
    "Science", "Current Affairs", "Uttar Pradesh Special", "Social Science",
]


@app.context_processor
def inject_globals():
    """Values every template can use without each route passing them by
    hand. Explicit render_template() kwargs (used by the pre-existing
    routes) still take precedence, so nothing already working changes."""
    return {
        "channel_name": TELEGRAM_CHANNEL_NAME,
        "channel_username": TELEGRAM_CHANNEL_USERNAME,
        "channel_url": TELEGRAM_CHANNEL_URL,
        "current_year": datetime.now(timezone.utc).year,
        "nav_exams": EXAMS,
    }


def score_range_bucket(score, total):
    # Use percentage for all quizzes so the result bands scale with quiz size.
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
            "headline": "🎉 EXCELLENT PERFORMANCE!",
            "eligible": "🟢 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
        }
    if bucket == "60-79":
        return {
            "headline": "🎯 GOOD PERFORMANCE!",
            "eligible": "🟢 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
        }
    return {
        "headline": "💪 KEEP GOING!",
        "eligible": "🟢 YOU ARE ELIGIBLE TO JOIN OUR PREPARATION COMMUNITY!",
    }


# --------------------------------------------------------------------------
# Page routes
# --------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template(
        "landing.html",
        channel_name=TELEGRAM_CHANNEL_NAME,
        channel_username=TELEGRAM_CHANNEL_USERNAME,
        channel_url=TELEGRAM_CHANNEL_URL,
        exams=EXAMS,
        latest_quizzes=list_available_quizzes()[:4],
    )


@app.route("/quizzes")
def quizzes_listing():
    return render_template("quizzes.html", quizzes=list_available_quizzes())


@app.route("/exams")
def exams_listing():
    return render_template("exams.html", exams=EXAMS)


@app.route("/exams/<slug>")
def exam_detail(slug):
    exam = EXAMS_BY_SLUG.get(slug)
    if exam is None:
        abort(404)
    return render_template("exam_detail.html", exam=exam, quizzes=list_available_quizzes())


@app.route("/study-material")
def study_material():
    return render_template(
        "study_material.html",
        categories=STUDY_MATERIAL_CATEGORIES,
        materials=[],  # data-driven; empty until real material is added
    )


@app.route("/current-affairs")
def current_affairs():
    return render_template("current_affairs.html", articles=[])  # empty until real content is added


@app.route("/robots.txt")
def robots_txt():
    root = request.url_root.rstrip("/")
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {root}/sitemap.xml",
    ])
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    root = request.url_root.rstrip("/")
    paths = ["/", "/quizzes", "/exams", "/study-material", "/current-affairs"]
    paths += [f"/exams/{e['slug']}" for e in EXAMS]
    paths += [f"/quiz/{q['quiz_id']}" for q in list_available_quizzes()]
    url_tags = "".join(f"<url><loc>{root}{p}</loc></url>" for p in paths)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{url_tags}</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@app.route("/quiz/<int:quiz_id>")
def quiz_page(quiz_id):
    quiz = load_quiz(quiz_id)
    if quiz is None:
        return render_template("not_found.html", quiz_id=quiz_id), 404
    return render_template(
        "quiz.html",
        quiz_id=quiz_id,
        title=quiz["title"],
        subtitle=quiz["subtitle"],
        channel_name=TELEGRAM_CHANNEL_NAME,
        channel_username=TELEGRAM_CHANNEL_USERNAME,
        channel_url=TELEGRAM_CHANNEL_URL,
    )


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------
@app.route("/api/quiz/<int:quiz_id>", methods=["GET"])
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


@app.route("/api/quiz/<int:quiz_id>/submit", methods=["POST"])
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
        # Already submitted -> return the stored score instead of rescoring,
        # so refresh/back-button/double-submit can't be used to retry.
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
    allowed = quiz["duration_seconds"] + SUBMIT_GRACE_SECONDS
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


@app.route("/api/analytics", methods=["POST"])
def api_analytics():
    if rate_limited(f"analytics:{client_ip()}"):
        return jsonify({"error": "Too many requests"}), 429

    body = request.get_json(silent=True) or {}
    event_name = body.get("event")
    quiz_id = body.get("quiz_id")
    score_range = body.get("score_range")  # e.g. "8-10", "6-7", "0-5"

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


@app.route("/api/analytics/summary", methods=["GET"])
def api_analytics_summary():
    """Simple aggregate view - handy while testing / for the site owner."""
    db = get_db()
    rows = db.execute(
        "SELECT event_name, score_range, COUNT(*) as cnt FROM analytics_events "
        "GROUP BY event_name, score_range"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.errorhandler(404)
def not_found(e):
    return render_template("not_found.html", quiz_id=None), 404


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    init_db()
