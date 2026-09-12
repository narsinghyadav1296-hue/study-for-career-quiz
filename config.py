"""
Central configuration for Study For Career.

Everything that differs between a laptop, a Render deployment, or any other
host lives here and is read from environment variables. Nothing secret is
hard-coded. Sensible local-development defaults are provided so the app
still runs out of the box with `python app.py`, exactly like before.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # --- Core Flask ---
    # In production this MUST be set via the SECRET_KEY env var, otherwise
    # sessions (including the admin login session) would reset on every
    # restart/deploy and could not be trusted. A random key is generated for
    # local dev only, so `python app.py` keeps working with zero setup.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # --- Database ---
    # Local development (default): SQLite, same file the existing
    # quiz-session tracking already used, extended with new CMS tables.
    # Production (Render or any host with an ephemeral filesystem): set
    # DATABASE_URL and PostgreSQL is used automatically instead - no other
    # configuration needed. See sfc/db.py for how both are kept behind one
    # identical interface.
    DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "quiz_app.db"))
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    DATABASE_BACKEND = "postgres" if DATABASE_URL else "sqlite"

    # Render (and most PaaS providers) set an environment variable marking
    # the runtime as production. We use that - plus the generic
    # ENVIRONMENT/FLASK_ENV vars for other hosts - to refuse to boot with a
    # silently-created local SQLite database in production (Section 20 of
    # the requirement): if this is production and DATABASE_URL is missing,
    # startup must fail loudly, not fall back quietly.
    IS_PRODUCTION = bool(
        os.environ.get("RENDER")
        or os.environ.get("ENVIRONMENT", "").lower() == "production"
        or os.environ.get("FLASK_ENV", "").lower() == "production"
        or os.environ.get("APP_ENV", "").lower() == "production"
    )

    # --- Existing JSON-based quiz directory (Quiz 1-4 and any future
    # hand-edited JSON quizzes). Left completely untouched. ---
    QUIZ_DIR = BASE_DIR / "data" / "quizzes"

    # New admin-created / DOCX-imported quizzes get public quiz_id values
    # starting at this offset, so they can never collide with the existing
    # JSON quiz_1.json..quiz_4.json (or any future hand-added JSON file with
    # a low id). Public id = internal DB row id + this offset.
    DB_QUIZ_ID_OFFSET = int(os.environ.get("DB_QUIZ_ID_OFFSET", 1000))

    # --- Telegram (unchanged from the original app) ---
    TELEGRAM_CHANNEL_URL = "https://t.me/studyforcareer_msn"
    TELEGRAM_CHANNEL_USERNAME = "@studyforcareer_msn"
    TELEGRAM_CHANNEL_NAME = "Study For Career"

    SUBMIT_GRACE_SECONDS = 15
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX_REQUESTS = 30

    # --- File storage (Section 14/7 of the requirement) ---
    # "local"  -> disk folder, fine for local development only. NEVER rely on
    #             this in production on Render (ephemeral filesystem).
    # "s3"     -> any S3-compatible provider (AWS S3, Cloudflare R2,
    #             Backblaze B2, MinIO...). Configure with the S3_* vars below.
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")

    LOCAL_UPLOAD_DIR = Path(os.environ.get("LOCAL_UPLOAD_DIR", BASE_DIR / "uploads"))

    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_REGION = os.environ.get("S3_REGION", "auto")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")  # set for R2/B2/MinIO
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    # Public base URL to build download links from, e.g. a CDN domain or the
    # bucket's public URL. Only used when the bucket/objects are public.
    S3_PUBLIC_BASE_URL = os.environ.get("S3_PUBLIC_BASE_URL", "")

    # --- Upload validation (Section 20) ---
    ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx"}
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", 25 * 1024 * 1024))  # 25 MB

    # --- Admin bootstrap (Section 12) ---
    # Used only by the `flask create-admin` CLI command, never read at
    # request time and never stored anywhere except the hashed password in
    # the database.
    BOOTSTRAP_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def get_config():
    return Config
