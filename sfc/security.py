"""
Admin authentication, session protection, CSRF protection, and upload
validation (Section 2 and Section 20 of the requirement).

No new heavy auth dependency is introduced (no Flask-Login/Flask-WTF) -
Flask's own signed session cookie (already relied on by SECRET_KEY) plus a
small manual CSRF token is enough for a single-role admin panel, is fully
testable offline, and is easy to extend later to multiple admin users
(the admin_users table already supports that - see Section 12's "future
multiple admin users" requirement).
"""
import functools
import re
import secrets

from flask import current_app, g, redirect, request, session, url_for, abort

from .db import get_db

SESSION_KEY = "admin_user_id"
CSRF_SESSION_KEY = "_csrf_token"


# ---------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------
def get_csrf_token():
    if CSRF_SESSION_KEY not in session:
        session[CSRF_SESSION_KEY] = secrets.token_hex(32)
    return session[CSRF_SESSION_KEY]


def validate_csrf(token):
    expected = session.get(CSRF_SESSION_KEY)
    return bool(expected) and bool(token) and secrets.compare_digest(expected, token)


def csrf_protect():
    """Call at the top of every admin POST route (or wire in as a
    before_request on the admin blueprint)."""
    if request.method == "POST":
        token = request.form.get("csrf_token", "")
        if not validate_csrf(token):
            abort(400, description="Invalid or missing CSRF token. Please refresh and try again.")


# ---------------------------------------------------------------------
# Admin session helpers
# ---------------------------------------------------------------------
def load_logged_in_admin():
    admin_id = session.get(SESSION_KEY)
    if admin_id is None:
        g.admin = None
        return
    db = get_db()
    g.admin = db.execute(
        "SELECT * FROM admin_users WHERE id = ? AND is_active = 1", (admin_id,)
    ).fetchone()


def login_admin(admin_row):
    session.clear()
    session[SESSION_KEY] = admin_row["id"]
    session.permanent = True


def logout_admin():
    session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("admin") is None:
            return redirect(url_for("admin_auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------
# Upload validation (Section 20 - file type validation, size limits,
# safe filenames, no executable uploads)
# ---------------------------------------------------------------------
def extension_of(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def validate_upload(file_storage, allowed_extensions, max_size_bytes):
    """Returns (ok, error_message). Never trusts the client-supplied
    Content-Type header alone - extension allow-listing plus a size cap
    covers the practical risk for this admin-only upload surface."""
    if file_storage is None or file_storage.filename == "":
        return False, "No file was selected."

    ext = extension_of(file_storage.filename)
    if ext not in allowed_extensions:
        return False, f"File type '.{ext}' is not allowed. Allowed: {', '.join(sorted(allowed_extensions))}"

    file_storage.stream.seek(0, 2)  # seek to end
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size == 0:
        return False, "The uploaded file is empty."
    if size > max_size_bytes:
        mb = max_size_bytes / (1024 * 1024)
        return False, f"File is too large. Maximum allowed size is {mb:.0f} MB."

    return True, ""


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text):
    text = (text or "").strip().lower()
    text = _slug_re.sub("-", text).strip("-")
    return text or secrets.token_hex(4)
