from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import random
from datetime import datetime

WEB_DIR = os.path.join(os.path.dirname(__file__), "../web")
STATIC_DIR = os.path.join(WEB_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
CORS(app)

STATE = {
    "sensors": {"temp": None, "humidity": None, "timestamp": None},
    "system": {"cpu": None, "ram": None, "ram_speed": None, "core_temp":  None, "timestamp": None},
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
    "fun": {"quote": None, "timestamp": None}
}

# ----- API ENDPOINTS -----

# Sensors
@app.post("/api/sensors")
def update_sensors():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["sensors"].update(data)
    return {"status": "ok"}

@app.get("/api/sensors")
def get_sensors():
    return jsonify(STATE["sensors"])

# System
@app.post("/api/system")
def update_system():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["system"].update(data)
    return {"status": "ok"}

@app.get("/api/system")
def get_system():
    return jsonify(STATE["system"])

# Network
@app.post("/api/network")
def update_network():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["network"].update(data)
    return {"status": "ok"}

@app.get("/api/network")
def get_network():
    return jsonify(STATE["network"])

# Weather
@app.post("/api/weather")
def update_weather():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["weather"].update(data)
    return {"status": "ok"}

@app.get("/api/weather")
def get_weather():
    return jsonify(STATE["weather"])

# Fun
@app.post("/api/fun")
def update_fun():
    data = request.get_json(silent=True) or {}
    data["timestamp"] = datetime.now().isoformat()
    STATE["fun"].update(data)
    return {"status": "ok"}

@app.get("/api/fun")
def get_fun():
    return jsonify(STATE["fun"])

# Full state
@app.get("/api/state")
def get_state():
    return jsonify(STATE)

# ----- SERVE WEBSITE -----

WEB_DIR = os.path.join(os.path.dirname(__file__), "../web")

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


# ----- RUN SERVER -----
if __name__ == "__main__":
    print("Dashboard running on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
