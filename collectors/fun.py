import os
import time
import random
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000")
URL = f"{BASE_URL}/api/fun"
INTERVAL = 20
MAX_BACKOFF = 60
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data" / "fun-data"

logging.info("SCRIPT=%s", Path(__file__).resolve())
logging.info("CWD=%s", Path.cwd())
logging.info("DATA_DIR=%s (exists=%s)", DATA_DIR, DATA_DIR.exists())
logging.info("QUOTES=%s (exists=%s)", DATA_DIR / "quotes.txt", (DATA_DIR / "quotes.txt").exists())
logging.info("INSULTS=%s (exists=%s)", DATA_DIR / "insults.txt", (DATA_DIR / "insults.txt").exists())

def read_lines(filename: str) -> list[str]:
    path = DATA_DIR / filename
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            logging.warning("%s is empty", path)
            return ["(empty file)"]
        return lines
    except FileNotFoundError:
        logging.error("Could not find %s", path)
        return ["(file not found)"]
    except Exception as e:
        logging.exception("Failed reading %s: %s", path, e)
        return ["(read error)"]

def post_payload(session: requests.Session, payload: dict) -> None:
    """POST payload using the given requests.Session. Raises on failure."""
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()


def coinflip() -> str:
    """Simulate a coin flip and return 'heads' or 'tails'."""
    return "heads" if random.randint(0, 1) == 0 else "tails"


def main() -> None:
    quotes = read_lines("quotes.txt")
    insults = read_lines("insults.txt")
    coinflip_results = coinflip()

    session = requests.Session()
    backoff = 1

    try:
        while True:
            payload = {
                "quote": random.choice(quotes),
                "insult": random.choice(insults),
                "coinflip": coinflip_results,
                "timestamp": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
            }

            try:
                post_payload(session, payload)
                logging.info("Sent fun data: %s", payload)
                backoff = 1
            except requests.RequestException as e:
                logging.error("Failed to send fun data: %s", e)
                sleep_time = min(backoff, MAX_BACKOFF)
                logging.info("Backing off for %s seconds", sleep_time)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, MAX_BACKOFF)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        logging.info("Fun collector stopping (user interrupt)")
    except Exception as e:
        logging.exception("Uncaught exception in fun collector: %s", e)


if __name__ == "__main__":
    main()
