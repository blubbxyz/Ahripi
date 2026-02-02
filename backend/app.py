from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import random
from datetime import datetime
from picamera2 import Picamera2




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
    "fun": {"quote": None, "timestamp": None},
    "camera": {"ok": False, "latest": None, "last_capture": None, "timestamp": None}


}


# ----- API ENDPOINTS -----

# cam
PHOTO_DIR = "/home/blubb/ahripi-dev/data/pi-cam"
os.makedirs(PHOTO_DIR, exist_ok=True)

picam2 = None

def get_camera():
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
    cam = get_camera()

    filename = datetime.now().strftime("photo_%Y-%m-%d_%H-%M-%S.jpg")
    path = os.path.join(PHOTO_DIR, filename)

    cam.capture_file(path)

    STATE["camera"]["latest"] = filename
    STATE["camera"]["last_capture"] = datetime.now().isoformat()
    STATE["camera"]["timestamp"] = datetime.now().isoformat()


    return jsonify({"status": "ok", "filename": filename, "url": f"/photos/{filename}"})

@app.get("/api/camera")
def get_camera_state():
    try:
        get_camera()
    except Exception as e:
        STATE["camera"]["ok"] = False
        STATE["camera"]["timestamp"] = datetime.now().isoformat()
        STATE["camera"]["error"] = str(e)
    return jsonify(STATE["camera"])


@app.get("/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(PHOTO_DIR, filename)


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

# ---- BUTTON ACTION ----
@app.post("/api/button/coinflip")
def button_action():
    data = request.get_json(silent=True) or {}
    print("coinflip_requested:", data)
    return {"status": "ok"}

# ----- RUN SERVER -----
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port)