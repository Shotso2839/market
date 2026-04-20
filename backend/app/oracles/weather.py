"""
app/oracles/weather.py
Оракул для погодных рынков через Open-Meteo API.

Open-Meteo — бесплатный, без регистрации, до 10 000 req/день.
Документация: https://open-meteo.com/en/docs

Для получения координат используем Open-Meteo Geocoding API.

oracle_params для рынка:
  {
    "type": "weather",
    "city": "Moscow",               # название города (английское)
    "latitude": 55.75,              # или координаты напрямую
    "longitude": 37.62,
    "date": "2026-05-20",           # дата события (YYYY-MM-DD)
    "question": "rain",             # "rain" | "temp_above" | "temp_below" |
                                    # "snow" | "sunny" | "wind_above"
    "target": 20.0,                 # для temp_above/below (°C), wind_above (km/h)
    "rain_threshold_mm": 1.0        # мм осадков для считать «дождь» (default: 1)
  }

Пример вопроса рынка:
  "Будет ли дождь в Москве 20 мая 2026?"
  oracle_params = {
    "type": "weather", "city": "Moscow",
    "date": "2026-05-20", "question": "rain"
  }
"""
import httpx
import logging
from datetime import date, datetime
from typing import Optional

from app.oracles.base import BaseOracle, OracleResult

log = logging.getLogger(__name__)

FORECAST_URL   = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL  = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL    = "https://archive-api.open-meteo.com/v1/archive"


class WeatherOracle(BaseOracle):
    name = "weather"

    async def resolve(self, market) -> OracleResult:
        params   = self._parse_params(market)
        question = params.get("question", "rain")
        target   = float(params.get("target", 0))
        target_date_str = params.get("date", "")

        if not target_date_str:
            return OracleResult(outcome=None, error="oracle_params.date is required (YYYY-MM-DD)")

        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            return OracleResult(outcome=None, error=f"Invalid date format: {target_date_str}")

        # Проверяем — дата уже прошла?
        today = date.today()
        if target_date > today:
            return OracleResult(
                outcome=None,
                source_value=f"Event date {target_date} has not arrived yet (today: {today})",
                source_name="Open-Meteo",
            )

        # Получаем координаты
        lat, lon, city_name = await self._get_coordinates(params)
        if lat is None:
            return OracleResult(outcome=None, error="Could not resolve city coordinates")

        # Для прошедших дат используем архивные данные
        weather = await self._fetch_weather(lat, lon, target_date)
        if weather is None:
            return OracleResult(outcome=None, error="Failed to fetch weather data")

        outcome = self._evaluate(question, weather, target, params)
        summary = self._weather_summary(weather, question)

        return OracleResult(
            outcome=outcome,
            confidence=1.0,
            source_value=f"{city_name} {target_date}: {summary}",
            source_name="Open-Meteo",
        )

    # ── Геокодирование ────────────────────────────────────────────────────────

    async def _get_coordinates(self, params: dict) -> tuple[Optional[float], Optional[float], str]:
        """Возвращает (lat, lon, city_name)."""
        lat = params.get("latitude")
        lon = params.get("longitude")
        if lat and lon:
            return float(lat), float(lon), params.get("city", "")

        city = params.get("city", "")
        if not city:
            return None, None, ""

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    GEOCODING_URL,
                    params={"name": city, "count": 1, "language": "en"},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])
            if not results:
                log.warning(f"City not found: {city}")
                return None, None, city

            r = results[0]
            return r["latitude"], r["longitude"], r.get("name", city)

        except httpx.HTTPError as e:
            log.warning(f"Geocoding error: {e}")
            return None, None, city

    # ── Получение данных о погоде ─────────────────────────────────────────────

    async def _fetch_weather(self, lat: float, lon: float, target_date: date) -> Optional[dict]:
        """
        Получает дневные метрики погоды для заданной даты.
        Для прошедших дат — архивный API. Для будущих — прогноз.
        """
        today = date.today()

        if target_date <= today:
            url = ARCHIVE_URL
        else:
            url = FORECAST_URL

        params = {
            "latitude":  lat,
            "longitude": lon,
            "start_date": target_date.isoformat(),
            "end_date":   target_date.isoformat(),
            "daily": [
                "precipitation_sum",          # мм осадков
                "temperature_2m_max",         # макс. температура °C
                "temperature_2m_min",         # мин. температура °C
                "temperature_2m_mean",        # средняя температура °C
                "snowfall_sum",               # мм снега
                "wind_speed_10m_max",         # макс. скорость ветра км/ч
                "weather_code",               # WMO weather code
                "sunshine_duration",          # секунды солнечного света
            ],
            "timezone": "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            daily = data.get("daily", {})
            if not daily or not daily.get("time"):
                return None

            # Берём первый (и единственный) день
            return {
                "precipitation_sum":   (daily.get("precipitation_sum")    or [0])[0] or 0,
                "temp_max":            (daily.get("temperature_2m_max")   or [0])[0] or 0,
                "temp_min":            (daily.get("temperature_2m_min")   or [0])[0] or 0,
                "temp_mean":           (daily.get("temperature_2m_mean")  or [0])[0] or 0,
                "snowfall_sum":        (daily.get("snowfall_sum")          or [0])[0] or 0,
                "wind_speed_max":      (daily.get("wind_speed_10m_max")   or [0])[0] or 0,
                "weather_code":        (daily.get("weather_code")          or [0])[0] or 0,
                "sunshine_duration":   (daily.get("sunshine_duration")     or [0])[0] or 0,
            }

        except httpx.HTTPError as e:
            log.warning(f"Open-Meteo error: {e}")
            return None

    # ── Оценка исхода ──────────────────────────────────────────────────────────

    def _evaluate(self, question: str, w: dict, target: float, params: dict) -> Optional[str]:
        rain_mm = w["precipitation_sum"]
        threshold = float(params.get("rain_threshold_mm", 1.0))

        if question == "rain":
            return "yes" if rain_mm >= threshold else "no"
        elif question == "no_rain":
            return "yes" if rain_mm < threshold else "no"
        elif question == "snow":
            return "yes" if w["snowfall_sum"] > 0 else "no"
        elif question == "temp_above":
            return "yes" if w["temp_max"] > target else "no"
        elif question == "temp_below":
            return "yes" if w["temp_min"] < target else "no"
        elif question == "temp_mean_above":
            return "yes" if w["temp_mean"] > target else "no"
        elif question == "sunny":
            # Солнечно = > 6 часов (21600 секунд) солнечного света
            return "yes" if w["sunshine_duration"] > 21600 else "no"
        elif question == "wind_above":
            return "yes" if w["wind_speed_max"] > target else "no"
        elif question == "extreme_weather":
            # WMO коды 80+ = ливень, гроза, снегопад, шторм
            return "yes" if w["weather_code"] >= 80 else "no"
        else:
            log.warning(f"Unknown weather question: {question}")
            return None

    def _weather_summary(self, w: dict, question: str) -> str:
        return (
            f"precip={w['precipitation_sum']:.1f}mm "
            f"temp={w['temp_min']:.0f}..{w['temp_max']:.0f}°C "
            f"wind={w['wind_speed_max']:.0f}km/h "
            f"snow={w['snowfall_sum']:.1f}mm"
        )
