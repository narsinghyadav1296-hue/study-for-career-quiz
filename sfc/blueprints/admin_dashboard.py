from flask import Blueprint, render_template

from ..db import get_db
from ..security import login_required
from .public import EXAMS

admin_dashboard_bp = Blueprint("admin_dashboard", __name__, url_prefix="/admin")


@admin_dashboard_bp.route("")
@admin_dashboard_bp.route("/")
@login_required
def dashboard():
    db = get_db()

    def count(sql):
        return db.execute(sql).fetchone()["cnt"]

    stats = {
        "quizzes": count("SELECT COUNT(*) AS cnt FROM quizzes WHERE deleted_at IS NULL"),
        "study_materials": count("SELECT COUNT(*) AS cnt FROM study_materials WHERE deleted_at IS NULL"),
        "current_affairs": count("SELECT COUNT(*) AS cnt FROM current_affairs WHERE deleted_at IS NULL"),
        "test_series": count("SELECT COUNT(*) AS cnt FROM test_series"),
        "courses": count("SELECT COUNT(*) AS cnt FROM courses"),
        "exams": len(EXAMS),
    }

    recent_uploads = db.execute(
        "SELECT original_filename, content_type, uploaded_at FROM media_files "
        "ORDER BY uploaded_at DESC LIMIT 8"
    ).fetchall()

    return render_template("admin/dashboard.html", stats=stats, recent_uploads=recent_uploads)
