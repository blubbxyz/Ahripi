from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import random
from datetime import datetime
from picamera2 import Picamera2
from flask import session
import sqlite3
import html
from functools import wraps

WEB_DIR = os.path.join(os.path.dirname(__file__), "../web")
STATIC_DIR = os.path.join(WEB_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
CORS(app)

app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-change-me")
app.config.update(
    SESSION_COOKIE_SECURE=True,      # only send cookie over https
    SESSION_COOKIE_HTTPONLY=True,    # JS can't read cookie
    SESSION_COOKIE_SAMESITE="Lax",   # helps prevent CSRF
)

# ---------------- GLOBAL STATE ----------------
# Everything the dashboard needs, kept in one place so the frontend can just ask for it.
# (Collectors / other services can POST updates into this dict.)
STATE = {
    "sensors": {"temp": None, "humidity": None, "timestamp": None},
    "system": {"cpu": None, "ram": None, "ram_speed": None, "core_temp": None, "timestamp": None},
    "network": {"rx_kbps": None, "tx_kbps": None, "timestamp": None},
    "weather": {
        "current_date": None,
        "outside_temp": None,
        "condition": None,
        "current_high_temp": None,
        "current_low_temp": None,
        "forecast_day1_date": None,
        "forecast_day1_avg_temp": None,
        "forecast_day1_high_temp": None,
        "forecast_day1_low_temp": None,
        "forecast_day2_date": None,
        "forecast_day2_avg_temp": None,
        "forecast_day2_high_temp": None,
        "forecast_day2_low_temp": None,
        "timestamp": None
    },
    "fun": {"quote": None, "timestamp": None, "insult": None, "coinflip": None},
    "camera": {"ok": False, "latest": None, "last_capture": None, "timestamp": None}
}

# ---------------- ADMIN AUTH ----------------
# Tiny session-based admin login: good enough to protect the "dangerous" endpoints.

def is_admin() -> bool:
    return session.get("is_admin") is True

@app.get("/api/admin/me")
def admin_me():
    return jsonify({"is_admin": is_admin()})

@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(silent=True) or {}
    pw = (data.get("password") or "").strip()

    real_pw = os.getenv("ADMIN_PASSWORD", "")
    if not real_pw or pw != real_pw:
        return jsonify({"error": "invalid login"}), 403

    session["is_admin"] = True
    return jsonify({"status": "ok"})

@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify({"status": "ok"})

# ---------------- COMMENTS ----------------
# Simple comment system backed by SQLite (plus a little spam + rate-limit glue).

COMMENTS_DB = os.path.join(os.path.dirname(__file__), "../data/comments.db")

def db():
    # Open a SQLite connection with dict-like rows (so we can do row["col"]).
    conn = sqlite3.connect(COMMENTS_DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_comments_db():
    # Make sure the folder + tables exist before we start handling requests.
    os.makedirs(os.path.dirname(COMMENTS_DB), exist_ok=True)
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            text TEXT NOT NULL,
            ip TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved INTEGER DEFAULT 1
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

def require_admin_session(fn):
    # Decorator: block endpoint unless you're logged in as admin.
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "admin required"}), 403
        return fn(*args, **kwargs)
    return wrapper

def check_rate_limit(ip: str, max_per_10min: int = 3) -> bool:
    # Basic anti-spam: allow only N comments per 10 minutes per IP.
    # Returns True if allowed, False if blocked.
    with db() as conn:
        cur = conn.execute("""
            SELECT COUNT(*) AS c
            FROM rate_limits
            WHERE ip = ?
              AND created_at >= datetime('now', '-10 minutes')
        """, (ip,))
        count = cur.fetchone()["c"]
        if count >= max_per_10min:
            return False

        conn.execute("INSERT INTO rate_limits (ip) VALUES (?)", (ip,))
        return True

def looks_like_spam(text: str) -> bool:
    # Super cheap "does this look like a bot" filter.
    # Not perfect, just trying to block obvious garbage.
    t = text.lower().strip()

    # more than 1 link is usually sus
    if t.count("http://") + t.count("https://") > 1:
        return True

    # one link and basically no real message
    if (("http://" in t) or ("https://" in t)) and len(t) < 25:
        return True

    # common spam keywords
    banned = ["free money", "crypto", "forex", "porn", "viagra", "casino", "betting"]
    if any(b in t for b in banned):
        return True

    # lots of the same character in a row -> classic bot spam pattern
    if len(text) >= 50:
        max_run = 1
        run = 1
        for i in range(1, len(text)):
            if text[i] == text[i - 1]:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 1
        if max_run >= 12:
            return True

    return False

init_comments_db()

@app.post("/api/comments")
def post_comment():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    name = (data.get("name") or "anon").strip()
    website = (data.get("website") or "").strip()  # honeypot field: real users won't fill this

    # If this field is filled, it's almost certainly a bot auto-filling forms.
    if website:
        return jsonify({"error": "blocked"}), 400

    if len(text) < 1:
        return jsonify({"error": "empty comment"}), 400
    if len(text) > 500:
        return jsonify({"error": "comment too long (max 500)"}), 400
    if len(name) > 32:
        name = name[:32]

    # Quick spam check before we even touch the DB.
    if looks_like_spam(text):
        return jsonify({"error": "blocked"}), 400

    # Best-effort IP (X-Forwarded-For if behind proxy, otherwise remote_addr).
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()

    if not check_rate_limit(ip):
        return jsonify({"error": "rate limited"}), 429

    # Escape user input so it can't inject HTML into the page.
    safe_text = html.escape(text)
    safe_name = html.escape(name)

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO comments (name, text, ip, approved) VALUES (?, ?, ?, 1)",
            (safe_name, safe_text, ip)
        )
        comment_id = cur.lastrowid

    return jsonify({"status": "ok", "id": comment_id})

@app.get("/api/comments")
def get_comments():
    # Return the newest approved comments (kept small to avoid mega payloads).
    with db() as conn:
        rows = conn.execute("""
            SELECT id, name, text, created_at
            FROM comments
            WHERE approved = 1
            ORDER BY id DESC
            LIMIT 50
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.delete("/api/comments/<int:comment_id>")
@require_admin_session
def delete_comment(comment_id: int):
    # Admin-only: nuke a comment by id.
    with db() as conn:
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return jsonify({"status": "ok"})



# ---------------- CAMERA ----------------
# Pi Camera integration: lazy-init the camera, capture files, and serve them back.

PHOTO_DIR = "/home/blubb/ahripi-dev/data/pi-cam"
os.makedirs(PHOTO_DIR, exist_ok=True)

picam2 = None

def get_camera():
    # Start the camera on first use (so the server can still boot if the camera isn't ready).
    global picam2
    if picam2 is None:
        cam = Picamera2()
        cam.configure(cam.create_still_configuration())
        cam.start()
        picam2 = cam

    STATE["camera"]["ok"] = True
    STATE["camera"]["timestamp"] = datetime.now().isoformat()
    return picam2

@app.post("/api/camera/capture")
def camera_capture():
    # Take a photo right now and store it on disk.
    cam = get_camera()

    filename = datetime.now().strftime("photo_%Y-%m-%d_%H-%M-%S.jpg")
    path = os.path.join(PHOTO_DIR, filename)

    cam.capture_file(path)

    # Update state so the UI knows what the latest photo is.
    STATE["camera"]["latest"] = filename
    STATE["camera"]["last_capture"] = datetime.now().isoformat()
    STATE["camera"]["timestamp"] = datetime.now().isoformat()

    return jsonify({"status": "ok", "filename": filename, "url": f"/photos/{filename}"})

@app.get("/api/camera")
def get_camera_state():
    # Ping the camera and report whether it's usable.
    try:
        get_camera()
    except Exception as e:
        STATE["camera"]["ok"] = False
        STATE["camera"]["timestamp"] = datetime.now().isoformat()
        STATE["camera"]["error"] = str(e)
    return jsonify(STATE["camera"])

@app.get("/photos/<path:filename>")
def serve_photo(filename):
    # Serve captured photos directly from the photo folder.
    return send_from_directory(PHOTO_DIR, filename)


# ---------------- SENSORS ----------------
# Endpoints used by the sensor collector to push updates + frontend to read them.

@app.post("/api/sensors")
def update_sensors():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["sensors"].update(data)
    return {"status": "ok"}

@app.get("/api/sensors")
def get_sensors():
    return jsonify(STATE["sensors"])


# ---------------- SYSTEM ----------------
# CPU/RAM/temp stats get pushed here by a collector.

@app.post("/api/system")
def update_system():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["system"].update(data)
    return {"status": "ok"}

@app.get("/api/system")
def get_system():
    return jsonify(STATE["system"])


# ---------------- NETWORK ----------------
# Network RX/TX stats get pushed here by a collector.

@app.post("/api/network")
def update_network():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["network"].update(data)
    return {"status": "ok"}

@app.get("/api/network")
def get_network():
    return jsonify(STATE["network"])


# ---------------- WEATHER ----------------
# Weather data (probably from an API / script) gets posted in, UI reads it out.

@app.post("/api/weather")
def update_weather():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["weather"].update(data)
    return {"status": "ok"}

@app.get("/api/weather")
def get_weather():
    return jsonify(STATE["weather"])


# ---------------- FUN ----------------
# Random "fun" stuff (quotes/insults/coinflip) pushed by a collector.

@app.post("/api/fun")
def update_fun():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["fun"].update(data)
    return {"status": "ok"}

@app.get("/api/fun")
def get_fun():
    return jsonify(STATE["fun"])


# ---------------- FULL STATE ----------------
# One endpoint to rule them all: frontend can fetch everything in one request.

@app.get("/api/state")
def get_state():
    return jsonify(STATE)


# ---------------- SERVE WEBSITE ----------------
# Serve the dashboard UI (and any static assets) straight from the web folder.

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")

@app.get("/web/static/<path:path>")
def serve_web_static(path):
    return send_from_directory(STATIC_DIR, path)

@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(WEB_DIR, path)


WEB2_DIR = os.path.join(os.path.dirname(__file__), "../web2")
WEB2_STATIC = os.path.join(WEB2_DIR, "static")

@app.get("/site2/")
def site2_index():
    # Second site/skin version (served under /site2/).
    return send_from_directory(WEB2_DIR, "index.html")

@app.get("/site2/static/<path:path>")
def site2_static(path):
    return send_from_directory(WEB2_STATIC, path)

@app.get("/site2/<path:path>")
def site2_files(path):
    return send_from_directory(WEB2_DIR, path)


# ---------------- BUTTON ACTION ----------------
# Placeholder endpoint: frontend can ping this when the coinflip button is pressed.

@app.post("/api/button/coinflip")
def button_action():
    data = request.get_json(silent=True) or {}
    print("coinflip_requested:", data)
    return {"status": "ok"}


# ---------------- RUN SERVER ----------------
# Local dev entrypoint (in production you'd usually run via gunicorn/systemd).

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port)
