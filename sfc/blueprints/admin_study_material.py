from flask import Blueprint, current_app, redirect, render_template, request, url_for

from ..db import get_db, now
from ..security import csrf_protect, get_csrf_token, login_required, validate_upload
from ..storage import get_storage, guess_mime_type, safe_unique_filename
from .public import EXAMS, STUDY_MATERIAL_CATEGORIES

admin_sm_bp = Blueprint("admin_study_material", __name__, url_prefix="/admin/study-material")


def _save_uploaded_file(file_storage, folder, content_type, content_id):
    """Validates + persists an uploaded file via the configured storage
    backend, records metadata in media_files, returns the new media id."""
    storage = get_storage()
    safe_name = safe_unique_filename(file_storage.filename)
    storage_key, size_bytes = storage.save(file_storage, folder, safe_name)
    db = get_db()
    cur = db.execute(
        "INSERT INTO media_files (original_filename, storage_backend, storage_key, "
        "mime_type, size_bytes, is_public, content_type, content_id, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (
            file_storage.filename,
            storage.name,
            storage_key,
            guess_mime_type(file_storage.filename),
            size_bytes,
            content_type,
            content_id,
            now(),
        ),
    )
    db.commit()
    return cur.lastrowid


@admin_sm_bp.route("")
@login_required
def list_materials():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM study_materials WHERE deleted_at IS NULL ORDER BY updated_at DESC"
    ).fetchall()
    return render_template("admin/study_material_list.html", materials=rows)


@admin_sm_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_material():
    return _form(material=None)


@admin_sm_bp.route("/<int:material_id>/edit", methods=["GET", "POST"])
@login_required
def edit_material(material_id):
    db = get_db()
    material = db.execute(
        "SELECT * FROM study_materials WHERE id = ? AND deleted_at IS NULL", (material_id,)
    ).fetchone()
    if material is None:
        return redirect(url_for("admin_study_material.list_materials"))
    return _form(material=material)


def _form(material):
    error = None
    if request.method == "POST":
        csrf_protect()
        title = (request.form.get("title") or "").strip()
        description = request.form.get("description", "")
        subject = request.form.get("subject", "")
        exam_slug = request.form.get("exam_slug", "")
        topic = request.form.get("topic", "")
        category = request.form.get("category", "")
        language = request.form.get("language", "")
        status = "published" if request.form.get("status") == "published" else "draft"
        publish_date = now() if status == "published" else None

        if not title:
            error = "Title is required."
        else:
            db = get_db()
            if material is None:
                cur = db.execute(
                    "INSERT INTO study_materials (title, description, subject, exam_slug, topic, "
                    "category, language, status, publish_date, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (title, description, subject, exam_slug, topic, category, language,
                     status, publish_date, now(), now()),
                )
                db.commit()
                material_id = cur.lastrowid
            else:
                material_id = material["id"]
                db.execute(
                    "UPDATE study_materials SET title=?, description=?, subject=?, exam_slug=?, "
                    "topic=?, category=?, language=?, status=?, "
                    "publish_date = COALESCE(publish_date, ?), updated_at=? WHERE id=?",
                    (title, description, subject, exam_slug, topic, category, language,
                     status, publish_date, now(), material_id),
                )
                db.commit()

            file_storage = request.files.get("file")
            if file_storage and file_storage.filename:
                ok, err = validate_upload(
                    file_storage,
                    current_app.config["ALLOWED_DOCUMENT_EXTENSIONS"],
                    current_app.config["MAX_UPLOAD_SIZE_BYTES"],
                )
                if not ok:
                    error = err
                else:
                    media_id = _save_uploaded_file(file_storage, "study-material", "study_material", material_id)
                    db.execute(
                        "UPDATE study_materials SET file_media_id=? WHERE id=?", (media_id, material_id)
                    )
                    db.commit()

            thumb_storage = request.files.get("thumbnail")
            if thumb_storage and thumb_storage.filename:
                ok, err = validate_upload(
                    thumb_storage,
                    current_app.config["ALLOWED_IMAGE_EXTENSIONS"],
                    current_app.config["MAX_UPLOAD_SIZE_BYTES"],
                )
                if ok:
                    thumb_media_id = _save_uploaded_file(thumb_storage, "study-material", "thumbnail", material_id)
                    db.execute(
                        "UPDATE study_materials SET thumbnail_media_id=? WHERE id=?", (thumb_media_id, material_id)
                    )
                    db.commit()
                elif not error:
                    error = err

            if not error:
                return redirect(url_for("admin_study_material.list_materials"))

    return render_template(
        "admin/study_material_form.html",
        material=material,
        exams=EXAMS,
        categories=STUDY_MATERIAL_CATEGORIES,
        error=error,
        csrf_token=get_csrf_token(),
    )


@admin_sm_bp.route("/<int:material_id>/toggle-publish", methods=["POST"])
@login_required
def toggle_publish(material_id):
    csrf_protect()
    db = get_db()
    row = db.execute("SELECT status FROM study_materials WHERE id=?", (material_id,)).fetchone()
    if row:
        new_status = "draft" if row["status"] == "published" else "published"
        publish_date_sql = "publish_date = COALESCE(publish_date, ?), " if new_status == "published" else ""
        db.execute(
            f"UPDATE study_materials SET status=?, {publish_date_sql}updated_at=? WHERE id=?",
            (new_status, now(), now(), material_id) if new_status == "published"
            else (new_status, now(), material_id),
        )
        db.commit()
    return redirect(url_for("admin_study_material.list_materials"))


@admin_sm_bp.route("/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(material_id):
    csrf_protect()
    db = get_db()
    db.execute("UPDATE study_materials SET deleted_at=? WHERE id=?", (now(), material_id))
    db.commit()
    return redirect(url_for("admin_study_material.list_materials"))
