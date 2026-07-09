"""Weather tool backed by Open-Meteo (no API key required).

Two-step: geocode the location name, then fetch current conditions + a short
forecast. Returns structured data plus a preformatted human answer so the
fast path can reply without another LLM call.
"""
from __future__ import annotations

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → human text.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


class WeatherError(RuntimeError):
    pass


async def get_weather(location: str) -> dict:
    """Current conditions + 3-day outlook for a location name."""
    async with httpx.AsyncClient(timeout=15) as client:
        geo = await client.get(GEOCODE_URL, params={"name": location, "count": 1, "language": "en"})
        geo.raise_for_status()
        results = (geo.json() or {}).get("results") or []
        if not results:
            raise WeatherError(f"couldn't find a place called '{location}'")
        place = results[0]

        fc = await client.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 3,
                "timezone": "auto",
            },
        )
        fc.raise_for_status()
        data = fc.json()

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    code = int(current.get("weather_code", -1))
    name = place.get("name", location)
    country = place.get("country", "")

    lines = [
        f"**Weather in {name}{', ' + country if country else ''}**",
        "",
        f"Right now: {current.get('temperature_2m', '?')}°C "
        f"(feels like {current.get('apparent_temperature', '?')}°C), "
        f"{_WMO.get(code, 'unknown conditions')}, "
        f"humidity {current.get('relative_humidity_2m', '?')}%, "
        f"wind {current.get('wind_speed_10m', '?')} km/h.",
    ]
    days = daily.get("time") or []
    if days:
        lines.append("")
        lines.append("Next days:")
        for i, day in enumerate(days):
            dcode = int((daily.get("weather_code") or [0] * len(days))[i])
            lines.append(
                f"- {day}: {(daily.get('temperature_2m_min') or ['?'] * len(days))[i]}–"
                f"{(daily.get('temperature_2m_max') or ['?'] * len(days))[i]}°C, "
                f"{_WMO.get(dcode, '?')}, "
                f"{(daily.get('precipitation_probability_max') or ['?'] * len(days))[i]}% chance of precipitation"
            )

    return {
        "location": {"name": name, "country": country,
                     "latitude": place.get("latitude"), "longitude": place.get("longitude")},
        "current": current,
        "daily": daily,
        "answer": "\n".join(lines),
    }
