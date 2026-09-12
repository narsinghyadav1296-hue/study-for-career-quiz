from flask import Blueprint, current_app, redirect, render_template, request, url_for

from ..db import get_db, now
from ..security import csrf_protect, get_csrf_token, login_required, validate_upload
from ..storage import get_storage, guess_mime_type, safe_unique_filename
from .public import EXAMS

admin_ca_bp = Blueprint("admin_current_affairs", __name__, url_prefix="/admin/current-affairs")


@admin_ca_bp.route("")
@login_required
def list_articles():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM current_affairs WHERE deleted_at IS NULL ORDER BY updated_at DESC"
    ).fetchall()
    return render_template("admin/current_affairs_list.html", articles=rows)


@admin_ca_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_article():
    return _form(article=None)


@admin_ca_bp.route("/<int:article_id>/edit", methods=["GET", "POST"])
@login_required
def edit_article(article_id):
    db = get_db()
    article = db.execute(
        "SELECT * FROM current_affairs WHERE id = ? AND deleted_at IS NULL", (article_id,)
    ).fetchone()
    if article is None:
        return redirect(url_for("admin_current_affairs.list_articles"))
    return _form(article=article)


def _form(article):
    error = None
    if request.method == "POST":
        csrf_protect()
        title = (request.form.get("title") or "").strip()
        article_date = request.form.get("article_date", "")
        category = request.form.get("category", "")
        exam_slug = request.form.get("exam_slug", "")
        short_summary = request.form.get("short_summary", "")
        full_content = request.form.get("full_content", "")
        important_facts = request.form.get("important_facts", "")
        source_reference = request.form.get("source_reference", "")
        status = "published" if request.form.get("status") == "published" else "draft"

        if not title:
            error = "Title is required."
        else:
            db = get_db()
            if article is None:
                cur = db.execute(
                    "INSERT INTO current_affairs (title, article_date, category, exam_slug, "
                    "short_summary, full_content, important_facts, source_reference, status, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (title, article_date, category, exam_slug, short_summary, full_content,
                     important_facts, source_reference, status, now(), now()),
                )
                db.commit()
                article_id = cur.lastrowid
            else:
                article_id = article["id"]
                db.execute(
                    "UPDATE current_affairs SET title=?, article_date=?, category=?, exam_slug=?, "
                    "short_summary=?, full_content=?, important_facts=?, source_reference=?, "
                    "status=?, updated_at=? WHERE id=?",
                    (title, article_date, category, exam_slug, short_summary, full_content,
                     important_facts, source_reference, status, now(), article_id),
                )
                db.commit()

            image_storage = request.files.get("image")
            if image_storage and image_storage.filename:
                ok, err = validate_upload(
                    image_storage,
                    current_app.config["ALLOWED_IMAGE_EXTENSIONS"],
                    current_app.config["MAX_UPLOAD_SIZE_BYTES"],
                )
                if ok:
                    storage = get_storage()
                    safe_name = safe_unique_filename(image_storage.filename)
                    storage_key, size_bytes = storage.save(image_storage, "current-affairs", safe_name)
                    cur = db.execute(
                        "INSERT INTO media_files (original_filename, storage_backend, storage_key, "
                        "mime_type, size_bytes, is_public, content_type, content_id, uploaded_at) "
                        "VALUES (?,?,?,?,?,1,?,?,?)",
                        (image_storage.filename, storage.name, storage_key,
                         guess_mime_type(image_storage.filename), size_bytes,
                         "current_affairs_image", article_id, now()),
                    )
                    db.commit()
                    db.execute(
                        "UPDATE current_affairs SET image_media_id=? WHERE id=?",
                        (cur.lastrowid, article_id),
                    )
                    db.commit()
                elif not error:
                    error = err

            if not error:
                return redirect(url_for("admin_current_affairs.list_articles"))

    return render_template(
        "admin/current_affairs_form.html",
        article=article,
        exams=EXAMS,
        error=error,
        csrf_token=get_csrf_token(),
    )


@admin_ca_bp.route("/<int:article_id>/toggle-publish", methods=["POST"])
@login_required
def toggle_publish(article_id):
    csrf_protect()
    db = get_db()
    row = db.execute("SELECT status FROM current_affairs WHERE id=?", (article_id,)).fetchone()
    if row:
        new_status = "draft" if row["status"] == "published" else "published"
        db.execute(
            "UPDATE current_affairs SET status=?, updated_at=? WHERE id=?",
            (new_status, now(), article_id),
        )
        db.commit()
    return redirect(url_for("admin_current_affairs.list_articles"))


@admin_ca_bp.route("/<int:article_id>/delete", methods=["POST"])
@login_required
def delete_article(article_id):
    csrf_protect()
    db = get_db()
    db.execute("UPDATE current_affairs SET deleted_at=? WHERE id=?", (now(), article_id))
    db.commit()
    return redirect(url_for("admin_current_affairs.list_articles"))
