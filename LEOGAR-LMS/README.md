# LEOGAR LMS (Flask + SQLite)

```
LEOGAR-LMS/
├── frontend/   index.html (single-page UI, served by Flask)
├── backend/    app.py (Flask API) · courses.py (YouTube links) · quizzes.py (25 tests) · requirements.txt
└── database/   schema.sql (SQLite; lms.db is created automatically on first start)
```

## Run locally
```
cd backend
pip install -r requirements.txt
cp .env.example .env      # set JWT_SECRET
python app.py             # http://localhost:3000
```

## Edit content
- backend/courses.py: replace every VIDEO_ID_xx with your real YouTube links.
- backend/quizzes.py: 10 MCQs per video (answers never leave the server).
- PASS_MARK in .env: pass percentage (default 60).

## Tables
users, video_progress, attempts, reports (Success Reports). Open lms.db with DB Browser for SQLite.

## Deploy on Render
1. Push this folder to GitHub.
2. Render > New > Blueprint > pick the repo (uses render.yaml). Or New > Web Service:
   Build: `pip install -r backend/requirements.txt`
   Start: `gunicorn --chdir backend app:app --workers 2 --bind 0.0.0.0:$PORT`
3. Environment: NODE_ENV=production, JWT_SECRET=<long random text>, DB_PATH=/data/lms.db, PASS_MARK=60.
4. Attach a Disk mounted at /data (otherwise users and progress are lost on redeploy). Disks need a paid plan.
5. Open the URL (HTTPS is automatic). Health check: /api/health.

## Flow (enforced on the server)
Watch video > mark watched > its 10-MCQ test unlocks > pass (60%) > next video unlocks. After all 5 lesson tests of a course are passed, the Success Report is issued (average score).
