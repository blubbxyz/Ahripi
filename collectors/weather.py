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

INTERVAL = 600
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


async def main() -> None:
    """Main loop: fetch weather and post periodically, with backoff on errors."""
    session = requests.Session()
    backoff = 1

    while True:
        weather_data = await fetch_weather(CITY)

        if not weather_data:
            sleep_time = min(backoff, MAX_BACKOFF)
            logging.info("Weather fetch failed; backing off for %s seconds", sleep_time)
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue

        payload = {
            "city": CITY,
            "current_date": weather_data["date"],
            "outside_temp": weather_data["temperature"],
            "condition": weather_data["condition"],
            "current_high_temp": weather_data["today_high"],
            "current_low_temp": weather_data["today_low"],
            "forecast_day1_date": weather_data["forecast"][0]["date"] if weather_data["forecast"] else None,
            "forecast_day1_avg_temp": weather_data["forecast"][0]["avg_temp"] if weather_data["forecast"] else None,
            "forecast_day1_high_temp": weather_data["forecast"][0]["high_temp"] if weather_data["forecast"] else None,
            "forecast_day1_low_temp": weather_data["forecast"][0]["low_temp"] if weather_data["forecast"] else None,
            "forecast_day2_date": weather_data["forecast"][1]["date"] if len(weather_data["forecast"]) > 1 else None,
            "forecast_day2_avg_temp": weather_data["forecast"][1]["avg_temp"] if len(weather_data["forecast"]) > 1 else None,
            "forecast_day2_high_temp": weather_data["forecast"][1]["high_temp"] if len(weather_data["forecast"]) > 1 else None,
            "forecast_day2_low_temp": weather_data["forecast"][1]["low_temp"] if len(weather_data["forecast"]) > 1 else None,
            "timestamp": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        }

        try:
            post_payload(session, payload)
            logging.info("Sent weather data: %s", payload)
            backoff = 1
            await asyncio.sleep(INTERVAL)
        except requests.RequestException as e:
            logging.error("Failed to send weather data: %s", e)
            sleep_time = min(backoff, MAX_BACKOFF)
            logging.info("Backing off for %s seconds", sleep_time)
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Weather collector stopping (user interrupt)")
