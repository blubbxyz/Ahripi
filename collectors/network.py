import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import psutil

URL = "http://localhost:5000/api/network"
INTERVAL = 1
MAX_BACKOFF = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


def post_payload(session: requests.Session, payload: dict) -> None:
    """POST payload using the given requests.Session. Raises on failure."""
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()


def main() -> None:
    session = requests.Session()
    backoff = 1

    last = psutil.net_io_counters()
    last_t = time.time()

    try:
        while True:
            time.sleep(INTERVAL)
            now = psutil.net_io_counters()
            now_t = time.time()

            dt = max(now_t - last_t, 1e-6)
            rx_kbps = (now.bytes_recv - last.bytes_recv) * 8 / dt / 1000
            tx_kbps = (now.bytes_sent - last.bytes_sent) * 8 / dt / 1000

            payload = {
                "rx_kbps": round(rx_kbps, 1),
                "tx_kbps": round(tx_kbps, 1),
                "timestamp": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
            }

            try:
                post_payload(session, payload)
                logging.info("Sent network data: %s", payload)
                backoff = 1
            except requests.RequestException as e:
                logging.error("Failed to send network data: %s", e)
                sleep_time = min(backoff, MAX_BACKOFF)
                logging.info("Backing off for %s seconds", sleep_time)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, MAX_BACKOFF)

            last, last_t = now, now_t

    except KeyboardInterrupt:
        logging.info("Network collector stopping (user interrupt)")
    except Exception as e:
        logging.exception("Uncaught exception in network collector: %s", e)


if __name__ == "__main__":
    main()
