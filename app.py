"""
Study For Career - entry point.

Kept intentionally tiny so existing run/deploy commands keep working
unchanged:
    python3 app.py
    gunicorn app:app
    flask create-admin   (reads this file as the Flask app via app.py's
                           top-level `app` variable)

All actual application code lives in the sfc/ package (see sfc/__init__.py
for the app factory, and sfc/blueprints/ for routes).
"""
import os

from sfc import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
