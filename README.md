# Study For Career — Telegram Quiz Web App

Mobile-first quiz web app for UPSC/UPPCS aspirants. Users take a 10-question,
3-minute timed quiz and are invited to join the **Study For Career** Telegram
channel afterwards, regardless of score.

- Landing → Start Quiz → 10 MCQs → 3-min Timer → Auto Result → Telegram CTA
- Answer keys never reach the browser — the server validates and scores.
- Quiz content lives in a JSON file, not hard-coded in the app.

## 1. Project Structure

```
quizapp/
├── app.py                     # Entry point only (creates the app via sfc.create_app)
├── config.py                  # All environment-variable configuration
├── requirements.txt
├── sfc/                       # Application package
│   ├── db.py                  # Dual-backend DB layer: SQLite (dev) / PostgreSQL (prod)
│   ├── storage.py             # File storage: local disk (dev) / S3-compatible (prod)
│   ├── security.py            # Admin auth, CSRF, upload validation
│   ├── quiz_service.py        # Merges JSON quizzes (1-4) + DB-backed admin quizzes
│   ├── docx_import.py         # DOCX question-paper parser
│   └── blueprints/            # public pages, quiz API, and all /admin routes
├── data/
│   ├── quizzes/
│   │   └── quiz_1.json ... quiz_4.json   # Existing quizzes - untouched, JSON-based
│   └── subjects/subjects.json            # Phase 2/3 subject list - untouched
├── templates/
│   ├── landing.html, quiz.html, ...      # Existing student-facing pages
│   └── admin/                            # Admin panel templates
└── static/
    ├── css/, js/                         # Existing student-facing assets
    └── admin/                            # Admin panel CSS
```

At runtime, in **local development** (no `DATABASE_URL` set), the app creates
`quiz_app.db` (SQLite) automatically - same as before, no setup needed. In
**production**, set `DATABASE_URL` and the app uses PostgreSQL instead - see
section 9 below.

## 2. Setup

Requires **Python 3.9+**.

```bash
cd quizapp
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Run

```bash
python3 app.py
```

The app starts on **http://localhost:5000** (or `0.0.0.0:5000`, reachable
from other devices on your network / phone for mobile testing).

To use a different port:

```bash
PORT=8080 python3 app.py
```

## 4. Quiz #1 URL

```
http://localhost:5000/quiz/1
```

The landing page (`http://localhost:5000/`) links to it with a
**"🚀 Start Quiz"** button. Share `/quiz/1` directly in your Telegram
group/channel — users land straight on the quiz intro, no channel-join
required before starting.

## 5. Where the Telegram CTA is configured

Channel name, username and link are set in **one place**, `app.py`, at the
top:

```python
TELEGRAM_CHANNEL_URL = "https://t.me/studyforcareer_msn"
TELEGRAM_CHANNEL_USERNAME = "@studyforcareer_msn"
TELEGRAM_CHANNEL_NAME = "Study For Career"
```

These are passed into `templates/landing.html` and `templates/quiz.html`,
which render the "🔵 JOIN STUDY FOR CAREER" button on the result screen
(and the badge on the landing page). Change the values above and every page
updates automatically — no need to hunt through HTML files.

## 6. How to add Quiz #2 (and beyond)

1. Create `data/quizzes/quiz_2.json`, copying the structure of
   `data/quizzes/quiz_1.json`:

   ```json
   {
     "quiz_id": 2,
     "title": "🔥 QUIZ TITLE",
     "subtitle": "Subtitle line",
     "description": "Short description",
     "duration_seconds": 180,
     "questions": [
       {
         "id": 1,
         "question": "Question text?",
         "options": ["A", "B", "C", "D"],
         "correct_answer": 0,
         "explanation": "Why the answer is correct (reserved for future use)."
       }
       // ... 9 more questions
     ]
   }
   ```

2. That's it — no code changes needed. `/quiz/2` and `/api/quiz/2` work
   immediately because quizzes are loaded dynamically by ID from
   `data/quizzes/quiz_<id>.json`.
3. `correct_answer` is a 0-based index into `options` and is **never sent to
   the browser** — only `/api/quiz/<id>/submit` (server-side) sees it.
4. An invalid/missing quiz ID (e.g. `/quiz/99`) shows a friendly
   "quiz not available" page instead of an error.

## 7. What's already handled

- **Timer & auto-submit**: client-side countdown auto-submits at 0; the
  server independently checks elapsed time against `duration_seconds` (+15s
  network grace) before scoring, so a manipulated client clock can't extend
  the real time limit.
- **Refresh / back button**: quiz progress (answers, current question,
  session id, start time) is kept in `sessionStorage`. Refreshing resumes
  exactly where the user left off, with the *real* remaining time (computed
  from the original start timestamp, not reset). A submitted quiz reloads
  straight to the result screen instead of restarting.
- **No double-scoring**: each session can only be scored once server-side;
  re-submitting returns the already-stored score.
- **Rate limiting**: a lightweight per-IP limiter (30 requests/minute per
  route group) protects the quiz-fetch, submit, and analytics endpoints.
- **Concurrent sessions**: every quiz load gets its own UUID session stored
  in SQLite, so multiple users (or multiple tabs) never interfere with each
  other.
- **Privacy-friendly analytics**: only `quiz_started`, `quiz_completed`
  (with score range, not exact answers), and `telegram_button_clicked` are
  logged — no names, phone numbers, or IPs are stored in analytics events
  (IP is only kept against the session row, for abuse/rate-limit purposes).
  View aggregate counts anytime at `/api/analytics/summary`.
- **Mobile-first UI**: single-column layout capped at 480px, large tap
  targets, Hindi + English text support, fast-loading (no external UI
  frameworks/CDNs).

## 8. Self-Test Results

All of the following were verified against a running instance before
delivery:

| # | Test | Result |
|---|------|--------|
| 1 | 10/10 score | ✅ Correct score + "EXCELLENT PERFORMANCE" message |
| 2 | 8/10 score | ✅ Correct score + "EXCELLENT PERFORMANCE" message |
| 3 | 6/10 score | ✅ Correct score + "GOOD PERFORMANCE" message |
| 4 | 0/10 score | ✅ Correct score + "KEEP GOING" message (never discouraging) |
| 5 | Timer expiry | ✅ Server rejects a late submission's answers as unscored (score 0) even if answers were technically correct, based on server-tracked session start time |
| 6 | Page refresh mid-quiz | ✅ Resumes same question/answers/timer via sessionStorage + server session |
| 7 | Back button | ✅ In-progress state persists via sessionStorage; a submitted quiz reloads to the result screen |
| 8 | Mobile layout | ✅ Responsive single-column layout, tested at narrow viewport widths |
| 9 | Telegram button | ✅ Links to `https://t.me/studyforcareer_msn`, click tracked via analytics |
| 10 | Invalid quiz URL | ✅ `/quiz/99` and `/api/quiz/99` both return friendly 404s |
| 11 | Multiple simultaneous sessions | ✅ Two concurrent sessions scored independently (verified: one scored 10/10, the other 0/10, correctly) |

Additional checks: double-submitting the same session doesn't rescore;
an invalid `session_id` on submit is rejected (HTTP 400); rate limiting
returns HTTP 429 after the threshold; analytics rejects unknown/unexpected
event names.

## 9. Deploying to a server / cloud

This is a standard Flask app, so any Python host works. A few common paths:

### Quick VPS (Ubuntu) with Gunicorn + Nginx
```bash
pip install gunicorn
gunicorn -w 4 -b 127.0.0.1:5000 app:app
```
Then reverse-proxy `example.com` → `127.0.0.1:5000` with Nginx, and put
Nginx behind HTTPS (e.g. via Certbot/Let's Encrypt).

### Render — exact deployment steps (recommended)

Render's web-service filesystem is **ephemeral** - any file written to disk
(including a SQLite database or an uploaded PDF) is lost on the next deploy
or restart. This project therefore uses:

- **PostgreSQL** (a Render "PostgreSQL" instance) for the database, instead
  of the local SQLite file.
- **S3-compatible object storage** (Cloudflare R2, AWS S3, Backblaze B2, ...)
  for uploaded files, instead of local disk (see `sfc/storage.py`).

Steps:

1. **Create a Render PostgreSQL instance**
   - Render dashboard → New → PostgreSQL → choose a name/region/plan → Create.
   - Once it's up, open it and copy the **Internal Database URL** (if your
     web service will be in the same Render account/region - faster, free
     internal networking) or the **External Database URL** (if connecting
     from elsewhere). It looks like:
     `postgresql://user:password@host:5432/dbname`

2. **Create the Render Web Service**
   - Render dashboard → New → Web Service → connect this repository.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`

3. **Set environment variables** on the Web Service (Render dashboard →
   your service → "Environment"):
   ```
   DATABASE_URL       = <the Postgres URL from step 1>
   SECRET_KEY         = <a long random string - see .env.example for how to generate one>
   ADMIN_EMAIL        = you@example.com
   ADMIN_PASSWORD     = <a strong password, used only once by the bootstrap command below>
   STORAGE_BACKEND    = s3
   S3_BUCKET          = <your bucket name>
   S3_REGION          = auto            (or your AWS region, e.g. ap-south-1)
   S3_ENDPOINT_URL    = <required for Cloudflare R2/B2/MinIO, e.g. https://<account-id>.r2.cloudflarestorage.com>
   S3_ACCESS_KEY_ID   = <your key>
   S3_SECRET_ACCESS_KEY = <your secret>
   S3_PUBLIC_BASE_URL = <optional - your CDN/public bucket URL, if the bucket is public>
   ```
   Render automatically sets a `RENDER` environment variable on every
   service it runs. The app uses this to **refuse to start** if
   `DATABASE_URL` is missing in this environment, rather than silently
   creating a throwaway local SQLite database that would be wiped on the
   next deploy (see `sfc/db.py` / `sfc/__init__.py`).

4. **Deploy.** On first boot the app automatically creates every database
   table it needs (`CREATE TABLE IF NOT EXISTS ...` - see `sfc/db.py`).
   Existing data is never touched by this step.

5. **Create the first admin account** (one-time). From the Render dashboard,
   open a **Shell** on the web service and run:
   ```bash
   flask create-admin
   ```
   This reads `ADMIN_EMAIL`/`ADMIN_PASSWORD` from the environment
   variables you set in step 3 and stores only a salted password hash in
   PostgreSQL - never the plaintext password. You can remove
   `ADMIN_PASSWORD` from the environment afterwards if you like; it's only
   read by this one-time command.

6. **Log in** at `https://<your-app>.onrender.com/admin/login`.

If `DATABASE_URL` is ever wrong, unreachable, or the `psycopg` driver isn't
installed, the app will fail to start with a clear error message naming the
problem (host/database, never the password) instead of starting up broken -
see the "Database startup check" in `sfc/db.py`.

### Local development stays exactly as before
Don't set `DATABASE_URL` locally and the app keeps using a local
`quiz_app.db` SQLite file automatically - no Postgres installation needed
for day-to-day development.

### Docker (optional, if your platform wants a container)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn
COPY . .
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```

### After deploying
Update your Telegram group/channel posts to point to:
```
https://yourdomain.com/quiz/1
```

## 10. Future Expansion (architecture already supports)

- **More quizzes**: just add `quiz_3.json`, `quiz_4.json`, ... — URLs and API
  routes work automatically for any numeric ID with a matching file.
- **Daily / subject-wise quiz**: add a `category`/`date` field to each quiz
  JSON and a small listing route; no structural rework needed.
- **Leaderboard**: the `sessions` table already stores `score` per session;
  add a `nickname` field and a `/api/leaderboard` aggregate query.
- **Hindi/English switch**: quiz JSON can carry both language variants per
  question; the frontend already renders whatever text it's given.
- **Detailed explanations**: the `explanation` field already exists in the
  quiz JSON schema and in the submit response's `per_question` array — the
  result screen can show it whenever you're ready to enable it.
- **Student login / admin dashboard**: SQLite schema is intentionally
  simple (`sessions`, `analytics_events`) so a `users` table and an
  admin-only view can be layered on without touching the quiz-taking flow.
- **Telegram Bot integration**: the JSON quiz format can be reused directly
  by a Telegram Bot (e.g. via `python-telegram-bot`) to post the same
  questions natively in-app.
- **Referral / UTM tracking**: add a `?ref=` query param capture in
  `quiz.html`/`quiz.js` and include it in the analytics payload — the
  `analytics_events` table can take an extra column at any time.
