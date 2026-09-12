"""
Database layer - dual backend.

Local development (default, no configuration needed):
    Plain stdlib sqlite3, exactly as before. `quiz_app.db` on disk.

Production (Render or any host with an ephemeral filesystem):
    PostgreSQL, selected automatically the moment DATABASE_URL is set.
    Uses the lightweight `psycopg` (v3) driver - no ORM. psycopg is only
    imported when DATABASE_URL is actually present, so local development
    never needs it installed.

Every other file in this project (blueprints, quiz_service, security,
the create-admin CLI command) keeps calling:

    db = get_db()
    db.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    cur = db.execute("INSERT INTO ... VALUES (?, ?)", (a, b))
    new_id = cur.lastrowid
    db.commit()

...completely unchanged, using SQLite's `?` placeholder style and
`cursor.lastrowid`, regardless of which backend is actually active. The
`_CompatConnection` / `_CompatCursor` wrappers below are what make that
possible:

- `?` placeholders are transparently rewritten to psycopg's `%s` style
  when talking to PostgreSQL.
- `cursor.lastrowid` is emulated for PostgreSQL by appending
  `RETURNING id` to plain INSERT statements (every table in this schema
  has an `id` primary key, except `sessions` which uses an
  application-generated `session_id` and never needs `lastrowid`).
- Rows are always dict-like (`row["title"]`) on both backends, matching
  the original `sqlite3.Row` behaviour used throughout the codebase.

Existing Quiz 1-4 (and any future hand-added JSON quiz) never touch this
database at all - see sfc/quiz_service.py.
"""
import re
import sqlite3
import time
from urllib.parse import urlparse

from flask import current_app, g

# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------
# Written once, for SQLite. The PostgreSQL version is generated from this
# automatically (see _to_postgres_schema) because the only syntax
# difference the current schema actually needs is the primary-key/
# auto-increment column - every other type used here (TEXT, INTEGER,
# REAL) is valid, identical SQL in both databases.
SCHEMA_SQLITE = """
-- ---------------------------------------------------------------------
-- Existing tables (unchanged from the original app.py) - quiz-attempt
-- tracking and privacy-friendly analytics. Do not modify their shape.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    quiz_id INTEGER NOT NULL,
    started_at REAL NOT NULL,
    submitted INTEGER NOT NULL DEFAULT 0,
    score REAL,
    client_ip TEXT
);

CREATE TABLE IF NOT EXISTS analytics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    quiz_id INTEGER,
    score_range TEXT,
    created_at REAL NOT NULL
);

-- ---------------------------------------------------------------------
-- New: Admin / CMS tables
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_login_at REAL
);

-- Generic file metadata. The actual bytes live in whatever storage backend
-- is configured (local disk for dev, S3-compatible object storage in
-- production) - this table never stores file content itself.
CREATE TABLE IF NOT EXISTS media_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    storage_backend TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    is_public INTEGER NOT NULL DEFAULT 1,
    content_type TEXT,
    content_id INTEGER,
    uploaded_by INTEGER,
    uploaded_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS study_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    subject TEXT,
    exam_slug TEXT,
    topic TEXT,
    category TEXT,
    language TEXT,
    file_media_id INTEGER,
    thumbnail_media_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    publish_date REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);

CREATE TABLE IF NOT EXISTS current_affairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    article_date TEXT,
    category TEXT,
    exam_slug TEXT,
    short_summary TEXT,
    full_content TEXT,
    important_facts TEXT,
    source_reference TEXT,
    image_media_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subtitle TEXT,
    description TEXT,
    exam_slug TEXT,
    subject TEXT,
    category TEXT,
    instructions TEXT,
    duration_seconds INTEGER NOT NULL DEFAULT 180,
    marks_correct REAL NOT NULL DEFAULT 1,
    marks_wrong REAL NOT NULL DEFAULT 0,
    marks_unattempted REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    question TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT NOT NULL,
    option_d TEXT NOT NULL,
    correct_answer INTEGER NOT NULL,
    explanation TEXT,
    topic TEXT,
    image_media_id INTEGER,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT,
    short_description TEXT,
    detailed_description TEXT,
    logo_media_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- Phase 2/3 - architecture ready now, no UI yet.
CREATE TABLE IF NOT EXISTS test_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    exam_slug TEXT,
    description TEXT,
    thumbnail_media_id INTEGER,
    instructions TEXT,
    is_paid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS test_series_quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_series_id INTEGER NOT NULL,
    quiz_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    exam_slug TEXT,
    thumbnail_media_id INTEGER,
    instructor TEXT,
    duration TEXT,
    is_paid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS course_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS course_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content_type TEXT,
    media_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0
);
"""


def _to_postgres_schema(sqlite_schema):
    """The only backend-specific syntax this schema actually needs is the
    primary-key/auto-increment column. TEXT/INTEGER/REAL and
    'CREATE TABLE IF NOT EXISTS' are valid, identical SQL on both engines."""
    return sqlite_schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


SCHEMA_POSTGRES = _to_postgres_schema(SCHEMA_SQLITE)


def _split_statements(schema_sql):
    """Split the schema into individual CREATE TABLE statements. We never
    rely on multi-statement execute() (not universally supported the same
    way across sqlite3/psycopg), so each statement is run separately.
    Full-line SQL comments are stripped first so a comment preceding a
    statement (in the same ';'-delimited chunk) doesn't cause the real
    statement after it to be discarded."""
    lines = [ln for ln in schema_sql.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines)
    statements = []
    for raw in cleaned.split(";"):
        stmt = raw.strip()
        if stmt:
            statements.append(stmt)
    return statements


# ---------------------------------------------------------------------
# Backend-agnostic row type (dict-like, exactly like sqlite3.Row's
# string-key access, e.g. row["title"])
# ---------------------------------------------------------------------
class Row(dict):
    pass


# ---------------------------------------------------------------------
# Tables whose primary key is NOT an auto-generated `id` column, so
# `cursor.lastrowid` emulation (appending `RETURNING id` on PostgreSQL)
# must never be attempted for them. Driven directly off SCHEMA_SQLITE
# above rather than a name guess, so it can never drift from the real
# schema: any table declared as `id INTEGER PRIMARY KEY AUTOINCREMENT`
# is auto-id; everything else (currently just `sessions`, which uses an
# application-generated `session_id`) is not.
def _tables_without_auto_id(schema_sql):
    all_tables = re.findall(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(",
        schema_sql,
        re.IGNORECASE,
    )
    auto_id_tables = set(
        re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\([^)]*?\bid\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            schema_sql,
            re.IGNORECASE | re.DOTALL,
        )
    )
    return {t for t in all_tables if t not in auto_id_tables}


TABLES_WITHOUT_AUTO_ID = _tables_without_auto_id(SCHEMA_SQLITE)


_INSERT_TABLE_RE = re.compile(
    r"""^\s*INSERT\s+INTO\s+["'\[]?(\w+)""",
    re.IGNORECASE,
)


def _insert_target_table(sql):
    """Extract the table name from an INSERT statement's own
    'INSERT INTO <table>' clause - not a generic substring search over
    the whole SQL text, so it can't be fooled by a column, value, or
    comment that happens to contain a table-like word."""
    match = _INSERT_TABLE_RE.match(sql)
    return match.group(1) if match else None


def _sqlite_row_factory(cursor, row):
    columns = [d[0] for d in cursor.description]
    return Row(zip(columns, row))


# ---------------------------------------------------------------------
# Compatibility wrappers so every existing call site
# (`db.execute("... ?", (x,))`, `cur.lastrowid`) keeps working unchanged
# regardless of backend.
# ---------------------------------------------------------------------
class _CompatCursor:
    def __init__(self, raw_cursor, backend):
        self._cursor = raw_cursor
        self.backend = backend
        self.lastrowid = None

    def execute(self, sql, params=()):
        if self.backend == "postgres":
            pg_sql = sql.replace("?", "%s")
            is_insert = pg_sql.lstrip().upper().startswith("INSERT")
            has_returning = "RETURNING" in pg_sql.upper()
            target_table = _insert_target_table(pg_sql) if is_insert else None
            table_has_auto_id = (
                target_table is not None and target_table not in TABLES_WITHOUT_AUTO_ID
            )
            append_returning = is_insert and not has_returning and table_has_auto_id
            if append_returning:
                pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
            self._cursor.execute(pg_sql, params)
            if append_returning:
                result = self._cursor.fetchone()
                self.lastrowid = result["id"] if result else None
            else:
                self.lastrowid = None
        else:
            self._cursor.execute(sql, params)
            self.lastrowid = self._cursor.lastrowid
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class _CompatConnection:
    def __init__(self, raw_conn, backend):
        self._conn = raw_conn
        self.backend = backend

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        return _CompatCursor(cursor, self.backend).execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------
def _connect_sqlite(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = _sqlite_row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return _CompatConnection(conn, "sqlite")


def _connect_postgres(database_url):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL is set (PostgreSQL mode) but the 'psycopg' package "
            "is not installed. Add 'psycopg[binary]' to requirements.txt and "
            "pip install it."
        ) from exc

    conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=False)
    return _CompatConnection(conn, "postgres")


def get_db():
    """Per-request connection - SQLite locally, PostgreSQL in production,
    chosen once per app via current_app.config['DATABASE_BACKEND']."""
    if "db" not in g:
        backend = current_app.config["DATABASE_BACKEND"]
        if backend == "postgres":
            g.db = _connect_postgres(current_app.config["DATABASE_URL"])
        else:
            g.db = _connect_sqlite(current_app.config["DB_PATH"])
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    """Create every table (legacy + new) if it doesn't already exist.
    Safe to call on every startup - existing data (SQLite file or
    PostgreSQL database) is never touched, only CREATE TABLE IF NOT EXISTS
    statements are run."""
    backend = app.config["DATABASE_BACKEND"]
    if backend == "postgres":
        conn = _connect_postgres(app.config["DATABASE_URL"])
        statements = _split_statements(SCHEMA_POSTGRES)
    else:
        conn = _connect_sqlite(app.config["DB_PATH"])
        statements = _split_statements(SCHEMA_SQLITE)

    for stmt in statements:
        conn.execute(stmt)
    conn.commit()
    conn.close()


def check_database_connection(app):
    """Startup health check (Section 19 of the requirement): run a trivial
    query against the configured database so a bad DATABASE_URL, an
    unreachable PostgreSQL host, or a missing driver fails loudly and
    clearly at boot time - not with a confusing error on the first
    request, and never by silently falling back to a different database."""
    backend = app.config["DATABASE_BACKEND"]
    try:
        if backend == "postgres":
            conn = _connect_postgres(app.config["DATABASE_URL"])
        else:
            conn = _connect_sqlite(app.config["DB_PATH"])
        conn.execute("SELECT 1")
        conn.close()
    except Exception as exc:
        target = _redact_database_url(app.config["DATABASE_URL"]) if backend == "postgres" else str(app.config["DB_PATH"])
        raise RuntimeError(
            f"Database startup check failed (backend={backend}, target={target}). "
            f"The application will not start until this is fixed. "
            f"Original error: {exc}"
        ) from exc


def _redact_database_url(url):
    """For error messages only - never log/print credentials."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
        db_name = parsed.path.lstrip("/") or "?"
        return f"{parsed.scheme}://***:***@{host}{port}/{db_name}"
    except Exception:
        return "***"


def now():
    return time.time()
