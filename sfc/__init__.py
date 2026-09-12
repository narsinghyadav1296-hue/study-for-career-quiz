import getpass
from datetime import datetime, timezone

from flask import Flask, g, render_template
from werkzeug.security import generate_password_hash

from config import Config, BASE_DIR
from .db import check_database_connection, close_db, get_db, init_db, now


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.from_object(config_class)

    # --- Fail fast & clearly if this is production but no DATABASE_URL was
    # given (Section 20 of the requirement) - never silently fall back to a
    # local SQLite file that Render's ephemeral filesystem would wipe out. ---
    if app.config["IS_PRODUCTION"] and not app.config["DATABASE_URL"]:
        raise RuntimeError(
            "Refusing to start: this looks like a production environment "
            "(RENDER/ENVIRONMENT/FLASK_ENV=production is set) but DATABASE_URL "
            "is not configured. Set DATABASE_URL to your PostgreSQL connection "
            "string in the environment (see .env.example). Local SQLite is "
            "only used automatically when DATABASE_URL is absent AND this is "
            "not detected as production."
        )

    # --- Startup database health check (Section 19) - a bad DATABASE_URL,
    # an unreachable PostgreSQL host, or a missing driver fails clearly here
    # instead of surfacing as a confusing error on the first request. ---
    check_database_connection(app)

    init_db(app)

    # --- teardown ---
    app.teardown_appcontext(close_db)

    # --- load the logged-in admin (if any) before every request ---
    @app.before_request
    def _load_admin():
        from .security import load_logged_in_admin

        load_logged_in_admin()

    # --- template globals (unchanged from the original app.py) ---
    @app.context_processor
    def inject_globals():
        from .security import get_csrf_token

        return {
            "channel_name": app.config["TELEGRAM_CHANNEL_NAME"],
            "channel_username": app.config["TELEGRAM_CHANNEL_USERNAME"],
            "channel_url": app.config["TELEGRAM_CHANNEL_URL"],
            "current_year": datetime.now(timezone.utc).year,
            "nav_exams": _exams_list(),
            "current_admin": g.get("admin"),
            "csrf_token": get_csrf_token(),
        }

    # --- blueprints ---
    from .blueprints.public import public_bp
    from .blueprints.quiz_api import quiz_api_bp
    from .blueprints.admin_auth import admin_auth_bp
    from .blueprints.admin_dashboard import admin_dashboard_bp
    from .blueprints.admin_study_material import admin_sm_bp
    from .blueprints.admin_current_affairs import admin_ca_bp
    from .blueprints.admin_quiz import admin_quiz_bp
    from .blueprints.admin_docx_import import admin_docx_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(quiz_api_bp)
    app.register_blueprint(admin_auth_bp)
    app.register_blueprint(admin_dashboard_bp)
    app.register_blueprint(admin_sm_bp)
    app.register_blueprint(admin_ca_bp)
    app.register_blueprint(admin_quiz_bp)
    app.register_blueprint(admin_docx_bp)

    # --- 404 (unchanged behaviour) ---
    @app.errorhandler(404)
    def not_found(e):
        return render_template("not_found.html", quiz_id=None), 404

    # --- CLI: one-time secure admin bootstrap (Section 12) ---
    @app.cli.command("create-admin")
    def create_admin_command():
        """Create the first admin user. Reads ADMIN_EMAIL / ADMIN_PASSWORD
        from the environment if set, otherwise prompts interactively.
        Never stores a plaintext password - only a salted hash."""
        email = app.config["BOOTSTRAP_ADMIN_EMAIL"] or input("Admin email: ").strip()
        password = app.config["BOOTSTRAP_ADMIN_PASSWORD"] or getpass.getpass("Admin password: ")
        if not email or not password:
            print("Email and password are both required.")
            return
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return
        with app.app_context():
            db = get_db()
            existing = db.execute("SELECT id FROM admin_users WHERE email = ?", (email.lower(),)).fetchone()
            if existing:
                print(f"An admin with email {email} already exists.")
                return
            db.execute(
                "INSERT INTO admin_users (email, password_hash, is_active, created_at) VALUES (?,?,1,?)",
                (email.lower(), generate_password_hash(password), now()),
            )
            db.commit()
        print(f"Admin account created for {email}. You can now log in at /admin/login")

    return app


def _exams_list():
    from .blueprints.public import EXAMS

    return EXAMS
