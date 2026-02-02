Perfect — here is the **compiled README as plain `.txt` content**.
You can save this directly as `README.txt`.

---

# PiNode Hub (Ahripi)

PiNode Hub is a self-hosted Raspberry Pi project that acts as a small production
and development hub. It combines a Flask backend, a browser-based dashboard,
background data collectors, and optional hardware features such as sensors and
a Pi camera.

The same repository is used for both a stable public website and an experimental
development environment, depending on the domain it is deployed to.

---

## DEPLOYMENT SETUP (TWO-SERVER HUB)

This project runs in two environments:

Production (stable)

* Domain: [www.pinode.dev](http://www.pinode.dev)
* Branch: main
* Only stable, tested code is deployed here
* Intended for public access

Development (unstable / experimental)

* Domain: dev.pinode.dev
* Shows new features and projects
* May contain unfinished or broken functionality
* Used for testing before merging into main

---

## PROJECT OVERVIEW

Backend

* Flask application located in backend/app.py
* Serves frontend files
* Exposes JSON API endpoints
* Handles admin authentication
* Manages comments (SQLite)
* Controls camera capture (Raspberry Pi only)

Frontend (Dashboard)

* Located in web/
* Displays system stats, network stats, weather, sensor data
* Polls backend APIs regularly
* Includes comment system and admin login

Second Page (Camera UI)

* Located in web2/
* Served under /site2/
* Triggers camera captures and displays images

Collectors

* Located in collectors/
* Run as background processes (often via systemd)
* Collect data and POST it to the backend API

Data Storage

* data/comments.db (SQLite comment database)
* data/fun-data/ (quotes and insults)
* data/pi-cam/ (captured camera images)

---

## API OVERVIEW

Collectors POST data to the backend:

* POST /api/system
* POST /api/network
* POST /api/sensors
* POST /api/weather
* POST /api/fun

Frontend reads data from:

* GET /api/system
* GET /api/network
* GET /api/sensors
* GET /api/weather
* GET /api/fun
* GET /api/state

Camera:

* POST /api/camera/capture
* GET /api/camera
* GET /photos/<filename>

Comments:

* POST /api/comments
* GET /api/comments
* DELETE /api/comments/<id> (admin only)

Admin authentication:

* POST /api/admin/login
* POST /api/admin/logout
* GET /api/admin/me

---

## ENVIRONMENT VARIABLES (.env)

A .env file is required in the project root.
It must NOT be committed to Git.

Required variables:

PORT

* Port the Flask backend runs on

API_BASE_URL

* Base URL used by collectors to POST data
* Usually a local address (e.g. [http://127.0.0.1:5001](http://127.0.0.1:5001))

SECRET_KEY

* Flask session signing key
* Must be a long random string

ADMIN_PASSWORD

* Password for admin login
* Used to delete comments

Example .env:

PORT=5001
API_BASE_URL=[http://127.0.0.1:5001](http://127.0.0.1:5001)
SECRET_KEY=change-me-to-a-long-random-string
ADMIN_PASSWORD=change-me

Create a .env.example file for reference instead of committing .env.

---

## SETUP

1. Create virtual environment
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

2. Hardware notes

* Camera features require Picamera2 and a working Pi camera
* Sensor features require a DHT11 sensor (GPIO pin 17 by default)

3. Start backend
   python backend/app.py

Dashboard will be available at:

* /        (main dashboard)
* /site2/  (camera page)

---

## RUNNING COLLECTORS

Collectors should be run continuously (often via systemd).

Examples:

python collectors/system.py
python collectors/network.py
python collectors/sensors.py
python collectors/weather.py
python collectors/fun.py

Make sure .env is loaded so API_BASE_URL is available.

---

## SECURITY NOTES

* Never commit .env
* Use strong values for SECRET_KEY and ADMIN_PASSWORD
* Run behind HTTPS when exposed publicly
* Camera endpoints can expose sensitive images
* Development domain may be unstable by design

---

## LICENSE

Personal / private project.
No warranty provided.

---

If you want next:

* README.md version
* .env.example file
* systemd service templates
* repo cleanup checklist

Just say the word.
