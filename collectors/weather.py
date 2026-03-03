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
LATITUDE = 53.63
LONGITUDE = 11.41

POST_INTERVAL = 10
FETCH_INTERVAL = 3600
MAX_BACKOFF = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


# ===== SOURCE 1: wttr.in (via python_weather) =====

async def fetch_wttr(city: str) -> dict | None:
    try:
        async with python_weather.Client(unit=python_weather.METRIC) as client:
            weather = await client.get(city)

            today_high = weather.daily_forecasts[0].highest_temperature if weather.daily_forecasts else None
            today_low = weather.daily_forecasts[0].lowest_temperature if weather.daily_forecasts else None

            forecast_days = []
            for i, daily in enumerate(weather):
                if i >= 2:
                    break
                avg_temp = None
                try:
                    avg_temp = (daily.highest_temperature + daily.lowest_temperature) / 2
                except Exception:
                    pass

                forecast_days.append({
                    "date": str(daily.date),
                    "avg_temp": round(avg_temp, 1) if avg_temp is not None else None,
                    "high_temp": daily.highest_temperature,
                    "low_temp": daily.lowest_temperature,
                })

            today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

            return {
                "source": "wttr.in",
                "date": today,
                "temperature": weather.temperature,
                "condition": weather.description,
                "today_high": today_high,
                "today_low": today_low,
                "forecast": forecast_days,
            }
    except Exception as e:
        logging.warning("wttr.in failed: %s", e)
        return None


# ===== SOURCE 2: Open-Meteo (fallback) =====

# https://open-meteo.com/en/docs#weather_code
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

def fetch_openmeteo(lat: float, lon: float) -> dict | None:
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weather_code"
            f"&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=Europe/Berlin"
            f"&forecast_days=3"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        current = data.get("current", {})
        daily = data.get("daily", {})

        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])

        weather_code = current.get("weather_code", -1)
        condition = WMO_CODES.get(weather_code, f"Code {weather_code}")

        forecast_days = []
        # Skip index 0 (today), take next 2 days
        for i in range(1, min(3, len(dates))):
            high = highs[i] if i < len(highs) else None
            low = lows[i] if i < len(lows) else None
            avg = round((high + low) / 2, 1) if high is not None and low is not None else None
            forecast_days.append({
                "date": dates[i],
                "avg_temp": avg,
                "high_temp": high,
                "low_temp": low,
            })

        today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

        return {
            "source": "open-meteo",
            "date": today,
            "temperature": current.get("temperature_2m"),
            "condition": condition,
            "today_high": highs[0] if highs else None,
            "today_low": lows[0] if lows else None,
            "forecast": forecast_days,
        }
    except Exception as e:
        logging.warning("Open-Meteo failed: %s", e)
        return None


# ===== FETCH WITH FALLBACK =====

async def fetch_weather() -> dict | None:
    # Try wttr.in first
    result = await fetch_wttr(CITY)
    if result:
        logging.info("Weather fetched from wttr.in")
        return result

    # Fallback to Open-Meteo
    logging.info("Falling back to Open-Meteo...")
    result = fetch_openmeteo(LATITUDE, LONGITUDE)
    if result:
        logging.info("Weather fetched from Open-Meteo (fallback)")
        return result

    logging.error("Both weather sources failed!")
    return None


# ===== PAYLOAD + POST =====

def build_payload(city: str, weather_data: dict) -> dict:
    forecast = weather_data.get("forecast") or []

    return {
        "city": city,
        "source": weather_data.get("source", "unknown"),
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


def post_payload(session: requests.Session, payload: dict) -> None:
    resp = session.post(URL, json=payload, timeout=5)
    resp.raise_for_status()


# ===== MAIN LOOP =====

async def main() -> None:
    session = requests.Session()

    cached_weather: dict | None = None
    last_fetch_ts: float = 0.0
    post_backoff = 1

    while True:
        now_mono = asyncio.get_running_loop().time()

        if cached_weather is None or (now_mono - last_fetch_ts) >= FETCH_INTERVAL:
            logging.info("Fetching weather for %s (lat=%.2f, lon=%.2f)", CITY, LATITUDE, LONGITUDE)
            fresh = await fetch_weather()
            if fresh is not None:
                cached_weather = fresh
                last_fetch_ts = now_mono
                logging.info("Weather cache updated (source: %s)", fresh.get("source"))
            else:
                logging.warning("All sources failed; keeping previous cache (if any).")

        if cached_weather is None:
            logging.info("No cached weather yet; waiting %s seconds...", POST_INTERVAL)
            await asyncio.sleep(POST_INTERVAL)
            continue

        payload = build_payload(CITY, cached_weather)

        try:
            post_payload(session, payload)
            logging.info("Posted weather (source: %s)", cached_weather.get("source"))
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