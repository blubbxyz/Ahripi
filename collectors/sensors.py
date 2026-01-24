import requests
import time
from datetime import datetime
import logging
from zoneinfo import ZoneInfo
from Freenove_DHT import DHT      

URL = "http://localhost:5000/api/sensors"
INTERVAL = 2
MAX_BACKOFF = 60
DHT_PIN = 17 
dht = DHT(DHT_PIN)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

def read_dht(dht):
    """Read DHT and return (temp, humidity) as floats or (None, None) on failure."""
    chk = dht.readDHT11()
    if chk == 0:
        try:
            humidity = dht.getHumidity()
            temp = dht.getTemperature()
            humidity = float(humidity)
            temp = float(temp)
            return temp, humidity
        except Exception as e:
            logging.warning("DHT read succeeded but values invalid: %s", e)
            return None, None
    else:
        logging.debug("DHT read returned invalid values")
        return None, None

def post_payload(session, payload):
    """POST payload using the given requests.Session. Raises on failure."""
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()
    return resp

def main():
    dht = DHT(DHT_PIN)
    session = requests.Session()
    backoff = 1

    try:
        while True:
            temp, humidity = read_dht(dht)
            payload = {
                "temp": temp,
                "humidity": humidity,
                "timestamp": datetime.now(ZoneInfo("Europe/Berlin")).isoformat()
            }

            try:
                post_payload(session, payload)
                logging.info("Sent sensor data: %s", payload)
                backoff = 1
            except requests.RequestException as e:
                logging.error("Failed to send sensor data: %s", e)
                sleep_time = min(backoff, MAX_BACKOFF)
                logging.info("Backing off for %s seconds", sleep_time)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, MAX_BACKOFF)
            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        logging.info("Sensors collector stopping (user interrupt)")
    except Exception as e:
        logging.exception("Uncaught exception in sensors collector: %s", e)

if __name__ == "__main__":
    main()