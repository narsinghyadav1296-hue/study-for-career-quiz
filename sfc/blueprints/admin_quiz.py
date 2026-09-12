from flask import Blueprint, current_app, redirect, render_template, request, url_for

from ..db import get_db, now
from ..security import csrf_protect, get_csrf_token, login_required
from .public import EXAMS

admin_quiz_bp = Blueprint("admin_quiz", __name__, url_prefix="/admin/quizzes")


@admin_quiz_bp.route("")
@login_required
def list_quizzes():
    db = get_db()
    rows = db.execute(
        "SELECT q.*, (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id) AS question_count "
        "FROM quizzes q WHERE deleted_at IS NULL ORDER BY updated_at DESC"
    ).fetchall()
    return render_template(
        "admin/quiz_list.html", quizzes=rows, quiz_id_offset=current_app.config["DB_QUIZ_ID_OFFSET"]
    )


@admin_quiz_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_quiz():
    return _meta_form(quiz=None)


@admin_quiz_bp.route("/<int:quiz_id>/edit", methods=["GET", "POST"])
@login_required
def edit_quiz(quiz_id):
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id=? AND deleted_at IS NULL", (quiz_id,)).fetchone()
    if quiz is None:
        return redirect(url_for("admin_quiz.list_quizzes"))
    return _meta_form(quiz=quiz)


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _meta_form(quiz):
    error = None
    if request.method == "POST":
        csrf_protect()
        title = (request.form.get("title") or "").strip()
        subtitle = request.form.get("subtitle", "")
        description = request.form.get("description", "")
        exam_slug = request.form.get("exam_slug", "")
        subject = request.form.get("subject", "")
        category = request.form.get("category", "")
        instructions = request.form.get("instructions", "")
        duration_minutes = _to_float(request.form.get("duration_minutes"), 3)
        duration_seconds = max(30, int(duration_minutes * 60))
        marks_correct = _to_float(request.form.get("marks_correct"), 1)
        marks_wrong = _to_float(request.form.get("marks_wrong"), 0)
        marks_unattempted = _to_float(request.form.get("marks_unattempted"), 0)
        status = "published" if request.form.get("status") == "published" else "draft"

        if not title:
            error = "Quiz title is required."
        else:
            db = get_db()
            if quiz is None:
                cur = db.execute(
                    "INSERT INTO quizzes (title, subtitle, description, exam_slug, subject, category, "
                    "instructions, duration_seconds, marks_correct, marks_wrong, marks_unattempted, "
                    "status, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'manual',?,?)",
                    (title, subtitle, description, exam_slug, subject, category, instructions,
                     duration_seconds, marks_correct, marks_wrong, marks_unattempted, status, now(), now()),
                )
                db.commit()
                new_id = cur.lastrowid
                return redirect(url_for("admin_quiz.manage_questions", quiz_id=new_id))
            else:
                db.execute(
                    "UPDATE quizzes SET title=?, subtitle=?, description=?, exam_slug=?, subject=?, "
                    "category=?, instructions=?, duration_seconds=?, marks_correct=?, marks_wrong=?, "
                    "marks_unattempted=?, status=?, updated_at=? WHERE id=?",
                    (title, subtitle, description, exam_slug, subject, category, instructions,
                     duration_seconds, marks_correct, marks_wrong, marks_unattempted, status, now(),
                     quiz["id"]),
                )
                db.commit()
                return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz["id"]))

    return render_template(
        "admin/quiz_form.html", quiz=quiz, exams=EXAMS, error=error, csrf_token=get_csrf_token()
    )


@admin_quiz_bp.route("/<int:quiz_id>/toggle-publish", methods=["POST"])
@login_required
def toggle_publish(quiz_id):
    csrf_protect()
    db = get_db()
    row = db.execute("SELECT status FROM quizzes WHERE id=?", (quiz_id,)).fetchone()
    if row:
        question_count = db.execute(
            "SELECT COUNT(*) AS cnt FROM quiz_questions WHERE quiz_id=?", (quiz_id,)
        ).fetchone()["cnt"]
        new_status = "draft" if row["status"] == "published" else "published"
        if new_status == "published" and question_count == 0:
            return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))
        db.execute("UPDATE quizzes SET status=?, updated_at=? WHERE id=?", (new_status, now(), quiz_id))
        db.commit()
    return redirect(url_for("admin_quiz.list_quizzes"))


@admin_quiz_bp.route("/<int:quiz_id>/delete", methods=["POST"])
@login_required
def delete_quiz(quiz_id):
    csrf_protect()
    db = get_db()
    db.execute("UPDATE quizzes SET deleted_at=?, status='draft' WHERE id=?", (now(), quiz_id))
    db.commit()
    return redirect(url_for("admin_quiz.list_quizzes"))


# --------------------------------------------------------------------
# Question management
# --------------------------------------------------------------------
@admin_quiz_bp.route("/<int:quiz_id>/questions", methods=["GET", "POST"])
@login_required
def manage_questions(quiz_id):
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id=? AND deleted_at IS NULL", (quiz_id,)).fetchone()
    if quiz is None:
        return redirect(url_for("admin_quiz.list_quizzes"))

    error = None
    if request.method == "POST":
        csrf_protect()
        question = (request.form.get("question") or "").strip()
        option_a = (request.form.get("option_a") or "").strip()
        option_b = (request.form.get("option_b") or "").strip()
        option_c = (request.form.get("option_c") or "").strip()
        option_d = (request.form.get("option_d") or "").strip()
        correct_answer = _to_int(request.form.get("correct_answer"), -1)
        explanation = request.form.get("explanation", "")
        topic = request.form.get("topic", "")

        if not question or not all([option_a, option_b, option_c, option_d]):
            error = "Question and all four options are required."
        elif correct_answer not in (0, 1, 2, 3):
            error = "Please select the correct option."
        else:
            next_position = db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM quiz_questions WHERE quiz_id=?", (quiz_id,)
            ).fetchone()["next_pos"]
            db.execute(
                "INSERT INTO quiz_questions (quiz_id, position, question, option_a, option_b, "
                "option_c, option_d, correct_answer, explanation, topic) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (quiz_id, next_position, question, option_a, option_b, option_c, option_d,
                 correct_answer, explanation, topic),
            )
            db.execute("UPDATE quizzes SET updated_at=? WHERE id=?", (now(), quiz_id))
            db.commit()
            return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))

    questions = db.execute(
        "SELECT * FROM quiz_questions WHERE quiz_id=? ORDER BY position ASC", (quiz_id,)
    ).fetchall()
    return render_template(
        "admin/quiz_questions.html", quiz=quiz, questions=questions, error=error, csrf_token=get_csrf_token()
    )


@admin_quiz_bp.route("/<int:quiz_id>/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required
def edit_question(quiz_id, question_id):
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id=? AND deleted_at IS NULL", (quiz_id,)).fetchone()
    question = db.execute(
        "SELECT * FROM quiz_questions WHERE id=? AND quiz_id=?", (question_id, quiz_id)
    ).fetchone()
    if quiz is None or question is None:
        return redirect(url_for("admin_quiz.list_quizzes"))

    error = None
    if request.method == "POST":
        csrf_protect()
        question_text = (request.form.get("question") or "").strip()
        option_a = (request.form.get("option_a") or "").strip()
        option_b = (request.form.get("option_b") or "").strip()
        option_c = (request.form.get("option_c") or "").strip()
        option_d = (request.form.get("option_d") or "").strip()
        correct_answer = _to_int(request.form.get("correct_answer"), -1)
        explanation = request.form.get("explanation", "")
        topic = request.form.get("topic", "")

        if not question_text or not all([option_a, option_b, option_c, option_d]):
            error = "Question and all four options are required."
        elif correct_answer not in (0, 1, 2, 3):
            error = "Please select the correct option."
        else:
            db.execute(
                "UPDATE quiz_questions SET question=?, option_a=?, option_b=?, option_c=?, "
                "option_d=?, correct_answer=?, explanation=?, topic=? WHERE id=?",
                (question_text, option_a, option_b, option_c, option_d, correct_answer,
                 explanation, topic, question_id),
            )
            db.execute("UPDATE quizzes SET updated_at=? WHERE id=?", (now(), quiz_id))
            db.commit()
            return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))

    return render_template(
        "admin/quiz_question_form.html", quiz=quiz, question=question, error=error, csrf_token=get_csrf_token()
    )


@admin_quiz_bp.route("/<int:quiz_id>/questions/<int:question_id>/delete", methods=["POST"])
@login_required
def delete_question(quiz_id, question_id):
    csrf_protect()
    db = get_db()
    db.execute("DELETE FROM quiz_questions WHERE id=? AND quiz_id=?", (question_id, quiz_id))
    db.execute("UPDATE quizzes SET updated_at=? WHERE id=?", (now(), quiz_id))
    db.commit()
    return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))


@admin_quiz_bp.route("/<int:quiz_id>/questions/<int:question_id>/move", methods=["POST"])
@login_required
def move_question(quiz_id, question_id):
    csrf_protect()
    direction = request.form.get("direction")
    db = get_db()
    rows = db.execute(
        "SELECT id, position FROM quiz_questions WHERE quiz_id=? ORDER BY position ASC", (quiz_id,)
    ).fetchall()
    ids = [r["id"] for r in rows]
    if question_id in ids:
        idx = ids.index(question_id)
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_idx < len(ids):
            id_a, id_b = ids[idx], ids[swap_idx]
            pos_a = rows[idx]["position"]
            pos_b = rows[swap_idx]["position"]
            db.execute("UPDATE quiz_questions SET position=? WHERE id=?", (pos_b, id_a))
            db.execute("UPDATE quiz_questions SET position=? WHERE id=?", (pos_a, id_b))
            db.commit()
    return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))
