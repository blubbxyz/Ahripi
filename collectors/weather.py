import asyncio
import logging
from datetime import datetime
import os
from zoneinfo import ZoneInfo

import requests
import python_weather

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000")
URL = f"{BASE_URL}/api/weather"
CITY = "Schwerin"

POST_INTERVAL = 10           
FETCH_INTERVAL = 3600         
MAX_BACKOFF = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

async def fetch_weather(city: str) -> dict | None:
    """Fetch current weather and a 2-day forecast.

    Returns a dict on success, or None on failure.
    """
    try:
        async with python_weather.Client(unit=python_weather.METRIC) as client:
            weather = await client.get(city)

            today_high = weather.daily_forecasts[0].highest_temperature if weather.daily_forecasts else None
            today_low = weather.daily_forecasts[0].lowest_temperature if weather.daily_forecasts else None

            forecast_days: list[dict] = []
            for i, daily in enumerate(weather):
                if i >= 2:
                    break

                avg_temp = None
                try:
                    avg_temp = (daily.highest_temperature + daily.lowest_temperature) / 2
                except Exception:
                    avg_temp = None

                forecast_days.append(
                    {
                        "date": str(daily.date),
                        "avg_temp": round(avg_temp, 1) if avg_temp is not None else None,
                        "high_temp": daily.highest_temperature,
                        "low_temp": daily.lowest_temperature,
                    }
                )

            today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

            return {
                "date": today,
                "temperature": weather.temperature,
                "condition": weather.description,
                "today_high": today_high,
                "today_low": today_low,
                "forecast": forecast_days,
            }
    except Exception as e:
        logging.error("Error fetching weather: %s", e)
        return None


def post_payload(session: requests.Session, payload: dict) -> None:
    """POST payload using the given requests.Session. Raises on failure."""
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()


def build_payload(city: str, weather_data: dict) -> dict:
    """Convert cached weather_data into the API payload format."""
    forecast = weather_data.get("forecast") or []

    return {
        "city": city,
        "current_date": weather_data.get("date"),
        "outside_temp": weather_data.get("temperature"),
        "condition": weather_data.get("condition"),
        "current_high_temp": weather_data.get("today_high"),
        "current_low_temp": weather_data.get("today_low"),

        "forecast_day1_date": forecast[0]["date"] if len(forecast) > 0 else None,
        "forecast_day1_avg_temp": forecast[0]["avg_temp"] if len(forecast) > 0 else None,
        "forecast_day1_high_temp": forecast[0]["high_temp"] if len(forecast) > 0 else None,
        "forecast_day1_low_temp": forecast[0]["low_temp"] if len(forecast) > 0 else None,

        "forecast_day2_date": forecast[1]["date"] if len(forecast) > 1 else None,
        "forecast_day2_avg_temp": forecast[1]["avg_temp"] if len(forecast) > 1 else None,
        "forecast_day2_high_temp": forecast[1]["high_temp"] if len(forecast) > 1 else None,
        "forecast_day2_low_temp": forecast[1]["low_temp"] if len(forecast) > 1 else None,

        "timestamp": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
    }


async def main() -> None:
    session = requests.Session()

    cached_weather: dict | None = None
    last_fetch_ts: float = 0.0
    post_backoff = 1

    while True:
        now_mono = asyncio.get_running_loop().time()


        if cached_weather is None or (now_mono - last_fetch_ts) >= FETCH_INTERVAL:
            logging.info("Fetching weather from python_weather (city=%s)", CITY)
            fresh = await fetch_weather(CITY)
            if fresh is not None:
                cached_weather = fresh
                last_fetch_ts = now_mono
                logging.info("Weather cache updated.")
            else:
                logging.warning(
                    "Weather fetch failed; keeping previous cache (if any). Next fetch attempt in %ss.",
                    POST_INTERVAL,
                )

        if cached_weather is None:
            logging.info("No cached weather yet; waiting %s seconds...", POST_INTERVAL)
            await asyncio.sleep(POST_INTERVAL)
            continue

        payload = build_payload(CITY, cached_weather)

        try:
            post_payload(session, payload)
            logging.info("Posted cached weather (fresh timestamp).")
            post_backoff = 1
            await asyncio.sleep(POST_INTERVAL)

        except requests.RequestException as e:
            logging.error("Failed to POST weather data: %s", e)
            sleep_time = min(post_backoff, MAX_BACKOFF)
            logging.info("POST backoff for %s seconds", sleep_time)
            await asyncio.sleep(sleep_time)
            post_backoff = min(post_backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Weather collector stopping (user interrupt)")
