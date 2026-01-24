import time
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import psutil

URL = "http://localhost:5000/api/system"
INTERVAL = 1
MAX_BACKOFF = 60
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def run_cmd(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except Exception:
        return "N/A"


def post_payload(session: requests.Session, payload: dict) -> None:
    """POST payload using the given session. Raises on failure."""
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()


def main() -> None:
    session = requests.Session()
    backoff = 1

    while True:
        now_berlin = datetime.now(ZoneInfo("Europe/Berlin"))
        loc_time = now_berlin.strftime("%H:%M:%S")

        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent

        freq_raw = run_cmd("vcgencmd measure_clock arm")
        try:
            freq_mhz = round(int(freq_raw.split("=")[1]) / 1_000_000, 2)
        except Exception:
            freq_mhz = None


        temp_raw = run_cmd("vcgencmd measure_temp")

        try:
            temp = temp_raw.replace("temp=", "").replace("'C", "")
            temp = float(temp)
        except Exception:
            temp = None

        payload = {
            "cpu": cpu,
            "ram": ram,
            "ram_speed": freq_mhz,
            "core_temp": temp,
            "time": loc_time,
            "timestamp": now_berlin.isoformat(),
        }

        try:
            post_payload(session, payload)
            logging.info("Sent system data: %s", payload)
            backoff = 1
        except requests.RequestException as e:
            logging.error("Failed to send system data: %s", e)
            sleep_time = min(backoff, MAX_BACKOFF)
            logging.info("Backing off for %s seconds", sleep_time)
            time.sleep(sleep_time)
            backoff = min(backoff * 2, MAX_BACKOFF)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("System collector stopping (user interrupt)")
    except Exception as e:
        logging.exception("Uncaught exception in system collector: %s", e)
