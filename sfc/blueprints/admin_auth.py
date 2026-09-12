from flask import Blueprint, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..db import get_db, now
from ..security import get_csrf_token, login_admin, logout_admin, validate_csrf

admin_auth_bp = Blueprint("admin_auth", __name__, url_prefix="/admin")


@admin_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("admin") is not None:
        return redirect(url_for("admin_dashboard.dashboard"))

    error = None
    if request.method == "POST":
        if not validate_csrf(request.form.get("csrf_token", "")):
            error = "Session expired, please try again."
        else:
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            db = get_db()
            admin_row = db.execute(
                "SELECT * FROM admin_users WHERE email = ? AND is_active = 1", (email,)
            ).fetchone()
            if admin_row is None or not check_password_hash(admin_row["password_hash"], password):
                error = "Invalid email or password."
            else:
                login_admin(admin_row)
                db.execute(
                    "UPDATE admin_users SET last_login_at = ? WHERE id = ?",
                    (now(), admin_row["id"]),
                )
                db.commit()
                next_url = request.args.get("next") or url_for("admin_dashboard.dashboard")
                if not next_url.startswith("/admin"):
                    next_url = url_for("admin_dashboard.dashboard")
                return redirect(next_url)

    return render_template("admin/login.html", error=error, csrf_token=get_csrf_token())


@admin_auth_bp.route("/logout", methods=["POST"])
def logout():
    if not validate_csrf(request.form.get("csrf_token", "")):
        return redirect(url_for("admin_dashboard.dashboard"))
    logout_admin()
    return redirect(url_for("admin_auth.login"))
