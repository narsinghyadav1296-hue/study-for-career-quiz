"""
Admin DOCX quiz import.

Flow (Section 6/13 of the requirement - never blindly publish):

    GET  /admin/quiz-import/docx            -> upload form (+ quiz meta)
    POST /admin/quiz-import/docx            -> parse the file, show a
                                                preview/edit screen; nothing
                                                is written to the database
                                                yet
    POST /admin/quiz-import/docx/confirm    -> admin has reviewed/corrected
                                                the preview and clicked
                                                "Import Quiz"; only now is a
                                                draft quiz + its questions
                                                created

The imported quiz always starts as **draft** - the admin still has to
publish it explicitly from the quiz list, after a final look via the
normal question-management screen.
"""
from flask import Blueprint, current_app, redirect, render_template, request, url_for

from ..db import get_db, now
from ..docx_import import parse_docx
from ..security import csrf_protect, extension_of, get_csrf_token, login_required
from .public import EXAMS

admin_docx_bp = Blueprint("admin_docx_import", __name__, url_prefix="/admin/quiz-import/docx")

MAX_DOCX_SIZE_BYTES = 15 * 1024 * 1024


@admin_docx_bp.route("", methods=["GET", "POST"])
@login_required
def upload():
    error = None
    if request.method == "POST":
        csrf_protect()
        title = (request.form.get("title") or "").strip()
        file_storage = request.files.get("docx_file")

        if not title:
            error = "Quiz title is required."
        elif file_storage is None or file_storage.filename == "":
            error = "Please choose a .docx file to upload."
        elif extension_of(file_storage.filename) != "docx":
            error = "Only .docx files are supported for import."
        else:
            file_storage.stream.seek(0, 2)
            size = file_storage.stream.tell()
            file_storage.stream.seek(0)
            if size == 0:
                error = "The uploaded file is empty."
            elif size > MAX_DOCX_SIZE_BYTES:
                error = "File is too large (max 15 MB)."
            else:
                try:
                    result = parse_docx(file_storage.stream)
                except Exception:
                    error = (
                        "This file could not be read as a .docx document. "
                        "Please make sure it is a valid Word file (not a .doc or scanned PDF)."
                    )
                else:
                    meta = {
                        "title": title,
                        "subtitle": request.form.get("subtitle", ""),
                        "description": request.form.get("description", ""),
                        "exam_slug": request.form.get("exam_slug", ""),
                        "subject": request.form.get("subject", ""),
                        "category": request.form.get("category", ""),
                        "instructions": request.form.get("instructions", ""),
                        "duration_minutes": request.form.get("duration_minutes", "3"),
                        "marks_correct": request.form.get("marks_correct", "1"),
                        "marks_wrong": request.form.get("marks_wrong", "0"),
                        "marks_unattempted": request.form.get("marks_unattempted", "0"),
                    }
                    return render_template(
                        "admin/docx_preview.html",
                        meta=meta,
                        questions=result.questions,
                        global_warnings=result.global_warnings,
                        valid_count=result.valid_count,
                        invalid_count=result.invalid_count,
                        csrf_token=get_csrf_token(),
                    )

    return render_template(
        "admin/docx_upload.html", exams=EXAMS, error=error, csrf_token=get_csrf_token()
    )


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@admin_docx_bp.route("/confirm", methods=["POST"])
@login_required
def confirm():
    csrf_protect()
    total = int(request.form.get("total_questions", 0))

    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("admin_docx_import.upload"))

    duration_seconds = max(30, int(_to_float(request.form.get("duration_minutes"), 3) * 60))

    db = get_db()
    cur = db.execute(
        "INSERT INTO quizzes (title, subtitle, description, exam_slug, subject, category, "
        "instructions, duration_seconds, marks_correct, marks_wrong, marks_unattempted, "
        "status, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'draft', "
        "'docx_import', ?, ?)",
        (
            title,
            request.form.get("subtitle", ""),
            request.form.get("description", ""),
            request.form.get("exam_slug", ""),
            request.form.get("subject", ""),
            request.form.get("category", ""),
            request.form.get("instructions", ""),
            duration_seconds,
            _to_float(request.form.get("marks_correct"), 1),
            _to_float(request.form.get("marks_wrong"), 0),
            _to_float(request.form.get("marks_unattempted"), 0),
            now(),
            now(),
        ),
    )
    quiz_id = cur.lastrowid

    position = 0
    skipped = 0
    for i in range(total):
        include = request.form.get(f"q_{i}_include") == "on"
        question_text = (request.form.get(f"q_{i}_question") or "").strip()
        option_a = (request.form.get(f"q_{i}_option_a") or "").strip()
        option_b = (request.form.get(f"q_{i}_option_b") or "").strip()
        option_c = (request.form.get(f"q_{i}_option_c") or "").strip()
        option_d = (request.form.get(f"q_{i}_option_d") or "").strip()
        correct_raw = request.form.get(f"q_{i}_correct_answer")
        explanation = request.form.get(f"q_{i}_explanation", "")

        if not include:
            continue
        if not question_text or not all([option_a, option_b, option_c, option_d]):
            skipped += 1
            continue
        try:
            correct_answer = int(correct_raw)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if correct_answer not in (0, 1, 2, 3):
            skipped += 1
            continue

        position += 1
        db.execute(
            "INSERT INTO quiz_questions (quiz_id, position, question, option_a, option_b, "
            "option_c, option_d, correct_answer, explanation) VALUES (?,?,?,?,?,?,?,?,?)",
            (quiz_id, position, question_text, option_a, option_b, option_c, option_d,
             correct_answer, explanation),
        )

    db.commit()
    return redirect(url_for("admin_quiz.manage_questions", quiz_id=quiz_id))
