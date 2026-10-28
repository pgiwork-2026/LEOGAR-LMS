import os, re, sqlite3, time
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from courses import COURSES
from quizzes import QUIZZES

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

PASS = int(os.getenv("PASS_MARK") or 60)
SECRET = os.getenv("JWT_SECRET") or "change-this-secret"
if os.getenv("NODE_ENV") == "production" and not os.getenv("JWT_SECRET"):
    raise SystemExit("JWT_SECRET is required in production")

DB_PATH = os.getenv("DB_PATH") or os.path.join(BASE, "..", "database", "lms.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.abspath(os.path.join(BASE, DB_PATH))
SCHEMA = os.path.join(BASE, "..", "database", "schema.sql")
FRONTEND = os.path.abspath(os.path.join(BASE, "..", "frontend"))

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024


# ---------- database ----------
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    c = connect()
    c.execute("PRAGMA journal_mode=WAL")
    with open(SCHEMA, encoding="utf-8") as f:
        c.executescript(f.read())
    c.commit()
    c.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c is not None:
        c.close()


init_db()


# ---------- helpers ----------
class ApiError(Exception):
    def __init__(self, code, message, field=None):
        self.code, self.message, self.field = code, message, field


@app.errorhandler(ApiError)
def on_api_error(e):
    body = {"error": e.message}
    if e.field:
        body["field"] = e.field
    return jsonify(body), e.code


@app.errorhandler(Exception)
def on_error(e):
    code = getattr(e, "code", None)
    if isinstance(code, int) and 400 <= code < 600:
        return jsonify({"error": getattr(e, "name", "Error")}), code
    app.logger.exception(e)
    return jsonify({"error": "Server error"}), 500


def make_token(uid):
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    return jwt.encode({"id": uid, "exp": exp}, SECRET, algorithm="HS256")


def auth(f):
    from functools import wraps

    @wraps(f)
    def w(*a, **k):
        try:
            h = request.headers.get("Authorization", "")
            g.uid = jwt.decode(h[7:], SECRET, algorithms=["HS256"])["id"]
        except Exception:
            return jsonify({"error": "Please log in again"}), 401
        return f(*a, **k)

    return w


def body():
    return request.get_json(silent=True) or {}


def find_course(cid):
    for c in COURSES:
        if c["id"] == cid:
            return c
    raise ApiError(404, "Course not found")


def to_int(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str) and re.fullmatch(r"\s*-?\d+\s*", x):
        return int(x)
    return None


def done(uid, cid):
    rows = db().execute(
        "SELECT video_idx v FROM video_progress WHERE user_id=? AND course_id=? ORDER BY v",
        (uid, cid),
    ).fetchall()
    return [r["v"] for r in rows]


def prog(uid, cid):
    d = db()
    r = d.execute(
        "SELECT report_id rid, score, issued_at d FROM reports WHERE user_id=? AND course_id=?",
        (uid, cid),
    ).fetchone()
    bests = []
    for v in range(5):
        b = d.execute(
            "SELECT MAX(score) b FROM attempts WHERE user_id=? AND course_id=? AND video_idx=? AND passed=1",
            (uid, cid, v),
        ).fetchone()["b"]
        bests.append(b)
    out = {
        "done": done(uid, cid),
        "bests": bests,
        "tp": [0 if b is None else 1 for b in bests],
        "passed": bool(r),
    }
    if r:
        out.update({"rid": r["rid"], "score": r["score"], "date": r["d"]})
    return out


def pub(u):
    return {k: u[k] for k in ("name", "phone", "email", "college")}


def base36(n):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = chars[r] + s
    return s or "0"


# ---------- routes ----------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "app": "LEOGAR"})


@app.post("/api/signup")
def signup():
    q = body()
    b = {k: str(q.get(k) or "").strip() for k in ("name", "phone", "college", "email", "pw", "pw2")}
    if len(b["name"]) < 3:
        raise ApiError(400, "Enter your full name", "name")
    if not re.fullmatch(r"[6-9][0-9]{9}", b["phone"]):
        raise ApiError(400, "Enter a valid 10-digit number", "phone")
    if not b["college"]:
        raise ApiError(400, "College name is required", "college")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", b["email"]):
        raise ApiError(400, "Enter a valid email", "email")
    if len(b["pw"]) < 6:
        raise ApiError(400, "Use at least 6 characters", "pw")
    if b["pw"] != b["pw2"]:
        raise ApiError(400, "Passwords do not match", "pw2")
    email = b["email"].lower()
    d = db()
    if d.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError(409, "This email is already registered", "email")
    try:
        cur = d.execute(
            "INSERT INTO users(name,phone,email,college,password_hash) VALUES(?,?,?,?,?)",
            (b["name"], b["phone"], email, b["college"], generate_password_hash(b["pw"])),
        )
        d.commit()
    except sqlite3.IntegrityError:
        raise ApiError(409, "This email is already registered", "email")
    return jsonify({"token": make_token(cur.lastrowid)})


@app.post("/api/login")
def login():
    q = body()
    email = str(q.get("email") or "").strip().lower()
    u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u:
        raise ApiError(404, "No account found. Please sign up.", "email")
    if not check_password_hash(u["password_hash"], str(q.get("pw") or "")):
        raise ApiError(401, "Incorrect password", "pw")
    return jsonify({"token": make_token(u["id"])})


@app.get("/api/me")
@auth
def me():
    u = db().execute("SELECT * FROM users WHERE id=?", (g.uid,)).fetchone()
    if not u:
        raise ApiError(401, "Please log in again")
    return jsonify({"user": pub(u), "progress": {c["id"]: prog(g.uid, c["id"]) for c in COURSES}})


@app.get("/api/courses")
@auth
def courses():
    return jsonify({"pass": PASS, "courses": [{k: v for k, v in c.items() if k != "quiz"} for c in COURSES]})


@app.post("/api/progress")
@auth
def progress():
    q = body()
    c = find_course(q.get("course"))
    v = to_int(q.get("video"))
    if v is None or v < 0 or v >= len(c["videos"]):
        raise ApiError(400, "Invalid video")
    if v > 0 and prog(g.uid, c["id"])["tp"][v - 1] != 1:
        raise ApiError(403, "Pass the previous lesson test first")
    d = db()
    d.execute(
        "INSERT OR IGNORE INTO video_progress(user_id,course_id,video_idx) VALUES(?,?,?)",
        (g.uid, c["id"], v),
    )
    d.commit()
    return jsonify({"done": done(g.uid, c["id"])})


def gate(cid, v):
    """A lesson test is locked on the SERVER until that video is marked as watched."""
    c = find_course(cid)
    if v < 0 or v >= len(c["videos"]):
        raise ApiError(400, "Invalid lesson")
    if v not in done(g.uid, c["id"]):
        raise ApiError(403, "Watch this video and mark it as watched to unlock its test")
    return c, QUIZZES[c["id"]][v]


@app.get("/api/test/<cid>/<int:v>")
@auth
def get_test(cid, v):
    _, z = gate(cid, v)
    return jsonify({"questions": [[r[0], r[1:5]] for r in z]})


@app.post("/api/test/<cid>/<int:v>")
@auth
def submit_test(cid, v):
    c, z = gate(cid, v)
    a = body().get("answers")
    if not isinstance(a, list) or len(a) != len(z):
        raise ApiError(400, "Answer all questions")
    correct = [r[5] for r in z]
    ok = sum(1 for i, x in enumerate(correct) if x == to_int(a[i]))
    score = round(ok / len(z) * 100)
    passed = score >= PASS
    d = db()
    d.execute(
        "INSERT INTO attempts(user_id,course_id,video_idx,score,passed) VALUES(?,?,?,?,?)",
        (g.uid, c["id"], v, score, 1 if passed else 0),
    )
    d.commit()
    p = prog(g.uid, c["id"])
    if passed and not p["passed"] and all(p["tp"]):
        avg = round(sum(p["bests"]) / len(p["bests"]))
        rid = "SR-" + c["id"].upper() + "-" + base36(int(time.time() * 1000))
        d.execute(
            "INSERT OR IGNORE INTO reports(report_id,user_id,course_id,score) VALUES(?,?,?,?)",
            (rid, g.uid, c["id"], avg),
        )
        d.commit()
        p = prog(g.uid, c["id"])
    return jsonify({"score": score, "passed": passed, "correct": correct, "correctCount": ok, "progress": p})


# ---------- frontend ----------
@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/<path:path>")
def static_files(path):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(FRONTEND, path)


if __name__ == "__main__":
    port = int(os.getenv("PORT") or 3000)
    print(f"LEOGAR running on port {port}")
    app.run(host="0.0.0.0", port=port)
