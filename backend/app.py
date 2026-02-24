from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import random
from datetime import datetime
from flask import session
import sqlite3
import html
from functools import wraps
from requests import RequestException
from json import JSONDecodeError



import requests
CAM_BASE = os.getenv("CAM_BASE", "http://127.0.0.1:5055")
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

# ---------------- HISTORY MAP ----------------
# Maps sensor names to (table, column) for each tier of data resolution.
# Used by /api/history to query the right table depending on the time range.
HISTORY_MAP = {
    "temp": {
        "raw":      ("sensor_readings",          "temp"),
        "minutely": ("sensor_readings_minutely",  "avg_temp"),
        "hourly":   ("sensor_readings_hourly",    "avg_temp"),
    },
    "humidity": {
        "raw":      ("sensor_readings",          "humidity"),
        "minutely": ("sensor_readings_minutely",  "avg_humidity"),
        "hourly":   ("sensor_readings_hourly",    "avg_humidity"),
    },
    "cpu": {
        "raw":      ("system_readings",          "cpu"),
        "minutely": ("system_readings_minutely",  "avg_cpu"),
        "hourly":   ("system_readings_hourly",    "avg_cpu"),
    },
    "ram": {
        "raw":      ("system_readings",          "ram"),
        "minutely": ("system_readings_minutely",  "avg_ram"),
        "hourly":   ("system_readings_hourly",    "avg_ram"),
    },
    "ram_speed": {
        "raw":      ("system_readings",          "ram_speed"),
        "minutely": ("system_readings_minutely",  "avg_ram_speed"),
        "hourly":   ("system_readings_hourly",    "avg_ram_speed"),
    },
    "core_temp": {
        "raw":      ("system_readings",          "core_temp"),
        "minutely": ("system_readings_minutely",  "avg_core_temp"),
        "hourly":   ("system_readings_hourly",    "avg_core_temp"),
    },
    "rx_kbps": {
        "raw":      ("network_readings",          "rx_kbps"),
        "minutely": ("network_readings_minutely",  "avg_rx_kbps"),
        "hourly":   ("network_readings_hourly",    "avg_rx_kbps"),
    },
    "tx_kbps": {
        "raw":      ("network_readings",          "tx_kbps"),
        "minutely": ("network_readings_minutely",  "avg_tx_kbps"),
        "hourly":   ("network_readings_hourly",    "avg_tx_kbps"),
    },
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

# ---------------- DATABASE ----------------

DATABASE = os.path.join(os.path.dirname(__file__), "../data/database.db")

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    with db() as conn:

        # ---- Comments + rate limits ----

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

        # ---- Raw readings (per-second) ----

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                temp REAL,
                humidity REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sensor_recorded
                ON sensor_readings(recorded_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cpu REAL,
                ram REAL,
                ram_speed REAL,
                core_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_system_recorded
                ON system_readings(recorded_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rx_kbps REAL,
                tx_kbps REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_network_recorded
                ON network_readings(recorded_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weather_date TEXT,
                outside_temp REAL,
                condition TEXT,
                current_high_temp REAL,
                current_low_temp REAL,
                forecast_day1_date TEXT,
                forecast_day1_avg_temp REAL,
                forecast_day1_high_temp REAL,
                forecast_day1_low_temp REAL,
                forecast_day2_date TEXT,
                forecast_day2_avg_temp REAL,
                forecast_day2_high_temp REAL,
                forecast_day2_low_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_weather_recorded
                ON weather_readings(recorded_at)
        """)

        # ---- Minutely aggregates (kept 30 days -> 1 year) ----

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_temp REAL, min_temp REAL, max_temp REAL,
                avg_humidity REAL, min_humidity REAL, max_humidity REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_cpu REAL, min_cpu REAL, max_cpu REAL,
                avg_ram REAL, min_ram REAL, max_ram REAL,
                avg_ram_speed REAL, min_ram_speed REAL, max_ram_speed REAL,
                avg_core_temp REAL, min_core_temp REAL, max_core_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_rx_kbps REAL, min_rx_kbps REAL, max_rx_kbps REAL,
                avg_tx_kbps REAL, min_tx_kbps REAL, max_tx_kbps REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ---- Hourly aggregates (kept beyond 1 year) ----

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_temp REAL, min_temp REAL, max_temp REAL,
                avg_humidity REAL, min_humidity REAL, max_humidity REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_cpu REAL, min_cpu REAL, max_cpu REAL,
                avg_ram REAL, min_ram REAL, max_ram REAL,
                avg_ram_speed REAL, min_ram_speed REAL, max_ram_speed REAL,
                avg_core_temp REAL, min_core_temp REAL, max_core_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_rx_kbps REAL, min_rx_kbps REAL, max_rx_kbps REAL,
                avg_tx_kbps REAL, min_tx_kbps REAL, max_tx_kbps REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

# ---------------- HELPER FUNCTIONS ----------------

def parse_range(range_str):
    """Convert '7d', '24h', '30m' into a SQLite datetime modifier like '-7 days'."""
    unit_map = {"m": "minutes", "h": "hours", "d": "days"}
    amount = range_str[:-1]
    unit = range_str[-1]
    if unit not in unit_map or not amount.isdigit():
        return None
    return f"-{amount} {unit_map[unit]}"

def get_tier(range_str):
    """Figure out which data tier to query based on the requested range."""
    amount = int(range_str[:-1])
    unit = range_str[-1]
    if unit == "m":
        days = amount / 1440
    elif unit == "h":
        days = amount / 24
    else:
        days = amount

    if days <= 30:
        return "raw"
    elif days <= 365:
        return "minutely"
    else:
        return "hourly"

def require_admin_session(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "admin required"}), 403
        return fn(*args, **kwargs)
    return wrapper

def check_rate_limit(ip: str, max_per_10min: int = 3) -> bool:
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
    t = text.lower().strip()
    if t.count("http://") + t.count("https://") > 1:
        return True
    if (("http://" in t) or ("https://" in t)) and len(t) < 25:
        return True
    banned = ["free money", "crypto", "forex", "porn", "viagra", "casino", "betting"]
    if any(b in t for b in banned):
        return True
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

init_db()

# ---------------- COMMENTS ----------------

@app.post("/api/comments")
def post_comment():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    name = (data.get("name") or "anon").strip()
    website = (data.get("website") or "").strip()

    if website:
        return jsonify({"error": "blocked"}), 400
    if len(text) < 1:
        return jsonify({"error": "empty comment"}), 400
    if len(text) > 500:
        return jsonify({"error": "comment too long (max 500)"}), 400
    if len(name) > 32:
        name = name[:32]
    if looks_like_spam(text):
        return jsonify({"error": "blocked"}), 400

    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
    if not check_rate_limit(ip):
        return jsonify({"error": "rate limited"}), 429

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
    with db() as conn:
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return jsonify({"status": "ok"})


# ---------------- CAMERA ----------------

@app.post("/api/camera/capture")
def camera_capture_proxy():
    try:
        r = requests.post(f"{CAM_BASE}/capture", timeout=60)
        return (r.text, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")})
    except RequestException as e:
        return jsonify({"status": "error", "error": f"camera collector unreachable: {e}"}), 502

@app.get("/api/camera")
def camera_state_proxy():
    try:
        r = requests.get(f"{CAM_BASE}/health", timeout=5)
        r.raise_for_status()
        data = r.json()
        STATE["camera"].update({
            "ok": data.get("ok", False),
            "latest": data.get("latest"),
            "last_capture": data.get("last_capture"),
            "timestamp": data.get("timestamp"),
        })
        return jsonify(data)
    except (RequestException, ValueError) as e:
        STATE["camera"]["ok"] = False
        return jsonify({"ok": False, "error": f"camera collector error: {e}"}), 502

@app.get("/photos/<path:filename>")
def photos_proxy(filename):
    try:
        r = requests.get(f"{CAM_BASE}/photos/{filename}", timeout=60)
        return (r.content, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/octet-stream")})
    except RequestException as e:
        return jsonify({"status": "error", "error": f"camera collector unreachable: {e}"}), 502


# ---------------- SENSORS ----------------

@app.post("/api/sensors")
def update_sensors():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["sensors"].update(data)

    with db() as conn:
        conn.execute(
            "INSERT INTO sensor_readings (temp, humidity) VALUES (?, ?)",
            (data.get("temp"), data.get("humidity"))
        )
    return {"status": "ok"}

@app.get("/api/sensors")
def get_sensors():
    return jsonify(STATE["sensors"])


# ---------------- SYSTEM ----------------

@app.post("/api/system")
def update_system():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["system"].update(data)

    with db() as conn:
        conn.execute("""
            INSERT INTO system_readings (cpu, ram, ram_speed, core_temp)
            VALUES (?, ?, ?, ?)
        """, (
            data.get("cpu"),
            data.get("ram"),
            data.get("ram_speed"),
            data.get("core_temp")
        ))
    return {"status": "ok"}

@app.get("/api/system")
def get_system():
    return jsonify(STATE["system"])


# ---------------- NETWORK ----------------

@app.post("/api/network")
def update_network():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["network"].update(data)

    with db() as conn:
        conn.execute("""
            INSERT INTO network_readings (rx_kbps, tx_kbps)
            VALUES (?, ?)
        """, (
            data.get("rx_kbps"),
            data.get("tx_kbps")
        ))
    return {"status": "ok"}

@app.get("/api/network")
def get_network():
    return jsonify(STATE["network"])


# ---------------- WEATHER ----------------

@app.post("/api/weather")
def update_weather():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["weather"].update(data)

    with db() as conn:
        conn.execute("""
            INSERT INTO weather_readings (
                weather_date, outside_temp, condition, current_high_temp, current_low_temp,
                forecast_day1_date, forecast_day1_avg_temp, forecast_day1_high_temp, forecast_day1_low_temp,
                forecast_day2_date, forecast_day2_avg_temp, forecast_day2_high_temp, forecast_day2_low_temp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("weather_date"),
            data.get("outside_temp"),
            data.get("condition"),
            data.get("current_high_temp"),
            data.get("current_low_temp"),
            data.get("forecast_day1_date"),
            data.get("forecast_day1_avg_temp"),
            data.get("forecast_day1_high_temp"),
            data.get("forecast_day1_low_temp"),
            data.get("forecast_day2_date"),
            data.get("forecast_day2_avg_temp"),
            data.get("forecast_day2_high_temp"),
            data.get("forecast_day2_low_temp")
        ))
    return {"status": "ok"}

@app.get("/api/weather")
def get_weather():
    return jsonify(STATE["weather"])


# ---------------- FUN ----------------

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

@app.get("/api/state")
def get_state():
    return jsonify(STATE)


# ---------------- HISTORY ----------------

@app.get("/api/history")
def get_history():
    sensor = request.args.get("sensor", "")
    range_str = request.args.get("range", "24h")

    if sensor not in HISTORY_MAP:
        return jsonify({"error": f"unknown sensor: {sensor}"}), 400

    modifier = parse_range(range_str)
    if not modifier:
        return jsonify({"error": "bad range (use e.g. 30m, 24h, 7d)"}), 400

    tier = get_tier(range_str)
    table, column = HISTORY_MAP[sensor][tier]
    time_col = "recorded_at" if tier == "raw" else ("minute" if tier == "minutely" else "hour")

    with db() as conn:
        rows = conn.execute(f"""
            SELECT {column} AS value, {time_col} AS time
            FROM {table}
            WHERE {time_col} >= datetime('now', ?)
            ORDER BY {time_col} ASC
        """, (modifier,)).fetchall()

    return jsonify([{"value": r["value"], "time": r["time"]} for r in rows])


# ---------------- SERVE WEBSITE ----------------

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
    return send_from_directory(WEB2_DIR, "index.html")

@app.get("/site2/static/<path:path>")
def site2_static(path):
    return send_from_directory(WEB2_STATIC, path)

@app.get("/site2/<path:path>")
def site2_files(path):
    return send_from_directory(WEB2_DIR, path)


# ---------------- BUTTON ACTION ----------------

@app.post("/api/button/coinflip")
def button_action():
    data = request.get_json(silent=True) or {}
    print("coinflip_requested:", data)
    return {"status": "ok"}


# ---------------- RUN SERVER ----------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port)