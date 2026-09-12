"""
Public (student-facing) routes.

Every URL here is byte-for-byte the same as the original app.py:
/, /quizzes, /exams, /exams/<slug>, /study-material, /current-affairs,
/quiz/<int:quiz_id>, /robots.txt, /sitemap.xml.

The only behavioural change from the original: /study-material and
/current-affairs now read published rows from the database instead of
always passing an empty list - the "content management" side of the
requirement. Nothing about the URL, template, or the quiz-taking flow
changes.
"""
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, render_template, request, send_from_directory

from ..db import get_db
from ..quiz_service import list_available_quizzes, load_quiz

public_bp = Blueprint("public", __name__)


# --------------------------------------------------------------------------
# Static reference data for exams (unchanged from the original app.py).
# Section 11 asks for this to *eventually* become database-driven, without
# changing existing public URLs/appearance - the `exams` table already
# exists (sfc/db.py) for that migration; EXAMS stays the source for Phase 1
# so nothing here regresses.
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


def _media_url(db, media_id):
    if not media_id:
        return None
    from ..storage import get_storage

    row = db.execute("SELECT storage_key FROM media_files WHERE id = ?", (media_id,)).fetchone()
    if not row:
        return None
    return get_storage().url(row["storage_key"])


@public_bp.route("/")
def home():
    return render_template(
        "landing.html",
        channel_name=current_app.config["TELEGRAM_CHANNEL_NAME"],
        channel_username=current_app.config["TELEGRAM_CHANNEL_USERNAME"],
        channel_url=current_app.config["TELEGRAM_CHANNEL_URL"],
        exams=EXAMS,
        latest_quizzes=list_available_quizzes()[:4],
    )


@public_bp.route("/quizzes")
def quizzes_listing():
    return render_template("quizzes.html", quizzes=list_available_quizzes())


@public_bp.route("/exams")
def exams_listing():
    return render_template("exams.html", exams=EXAMS)


@public_bp.route("/exams/<slug>")
def exam_detail(slug):
    exam = EXAMS_BY_SLUG.get(slug)
    if exam is None:
        abort(404)
    return render_template("exam_detail.html", exam=exam, quizzes=list_available_quizzes())


@public_bp.route("/study-material")
def study_material():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM study_materials WHERE status = 'published' AND deleted_at IS NULL "
        "ORDER BY COALESCE(publish_date, created_at) DESC"
    ).fetchall()
    materials = []
    for row in rows:
        materials.append(
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"] or "",
                "subject": row["subject"] or "",
                "exam_slug": row["exam_slug"] or "",
                "topic": row["topic"] or "",
                "category": row["category"] or "",
                "language": row["language"] or "",
                "file_url": _media_url(db, row["file_media_id"]),
                "thumbnail_url": _media_url(db, row["thumbnail_media_id"]),
            }
        )
    return render_template(
        "study_material.html",
        categories=STUDY_MATERIAL_CATEGORIES,
        materials=materials,
    )


@public_bp.route("/current-affairs")
def current_affairs():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM current_affairs WHERE status = 'published' AND deleted_at IS NULL "
        "ORDER BY COALESCE(article_date, created_at) DESC"
    ).fetchall()
    articles = []
    for row in rows:
        articles.append(
            {
                "id": row["id"],
                "title": row["title"],
                "date": row["article_date"] or "",
                "category": row["category"] or "",
                "exam_slug": row["exam_slug"] or "",
                "short_summary": row["short_summary"] or "",
                "full_content": row["full_content"] or "",
                "important_facts": row["important_facts"] or "",
                "source_reference": row["source_reference"] or "",
                "image_url": _media_url(db, row["image_media_id"]),
            }
        )
    return render_template("current_affairs.html", articles=articles)


@public_bp.route("/robots.txt")
def robots_txt():
    root = request.url_root.rstrip("/")
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {root}/sitemap.xml",
    ])
    return Response(body, mimetype="text/plain")


@public_bp.route("/sitemap.xml")
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


@public_bp.route("/quiz/<int:quiz_id>")
def quiz_page(quiz_id):
    quiz = load_quiz(quiz_id)
    if quiz is None:
        return render_template("not_found.html", quiz_id=quiz_id), 404
    return render_template(
        "quiz.html",
        quiz_id=quiz_id,
        title=quiz["title"],
        subtitle=quiz["subtitle"],
        channel_name=current_app.config["TELEGRAM_CHANNEL_NAME"],
        channel_username=current_app.config["TELEGRAM_CHANNEL_USERNAME"],
        channel_url=current_app.config["TELEGRAM_CHANNEL_URL"],
    )


@public_bp.route("/media/<path:storage_key>")
def media_local(storage_key):
    """Only ever used when STORAGE_BACKEND=local (development). In
    production (STORAGE_BACKEND=s3) file URLs point straight at the
    object-storage provider / CDN and never hit this route."""
    if current_app.config["STORAGE_BACKEND"] != "local":
        abort(404)
    base_dir = Path(current_app.config["LOCAL_UPLOAD_DIR"]).resolve()
    full_path = (base_dir / storage_key).resolve()
    if base_dir not in full_path.parents and full_path != base_dir:
        abort(404)
    if not full_path.exists():
        abort(404)
    return send_from_directory(base_dir, storage_key)
