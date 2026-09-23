"""Live weather data for Gandalf.

Fetches up-to-date weather from the free Open-Meteo API (no key required) and
produces a short Turkish summary with Praktik advice. Used both by the Telegram
/Gemini brain (gemini_handler.get_weather) and the local/voice path
(actions.weather_report.weather_action) so both channels give the same live,
spoken answer instead of merely opening a browser tab.
"""

import requests

# Misrecognised / variant city names (Vosk mangles Turkish names in voice) and
# Turkish aliases mapped onto the real city name used for geocoding.
_CITY_FIX = {
    "tania": "Antalya",
    "tanya": "Antalya",
    "taniya": "Antalya",
    "antalia": "Antalya",
    "antaliya": "Antalya",
    "istanbol": "Istanbul",
    "istambul": "Istanbul",
    "istanbu": "Istanbul",
    "istanbul": "Istanbul",
}


def normalize_city(city: str) -> str:
    low = str(city or "").strip().lower()
    return _CITY_FIX.get(low, str(city or "").strip())


# WMO weather codes (weather_code) -> (short phrase, rain?, snow?)
_WEATHER_CODES = {
    0: ("clear", False, False),
    1: ("few clouds", False, False),
    2: ("partly cloudy", False, False),
    3: ("overcast", False, False),
    45: ("foggy", False, False),
    48: ("frosty/foggy", False, False),
    51: ("light drizzle", True, False),
    53: ("drizzle", True, False),
    55: ("heavy drizzle", True, False),
    56: ("freezing drizzle", True, False),
    57: ("heavy freezing drizzle", True, False),
    61: ("light rain", True, False),
    63: ("rainy", True, False),
    65: ("heavy rain", True, False),
    66: ("freezing rain", True, False),
    67: ("heavy freezing rain", True, False),
    71: ("light snow", True, True),
    73: ("snowy", True, True),
    75: ("heavy snow", True, True),
    77: ("snow grains", True, True),
    80: ("light showers", True, False),
    81: ("showers", True, False),
    82: ("heavy showers", True, False),
    85: ("snow showers", True, True),
    86: ("heavy snow showers", True, True),
    95: ("thunderstorm", True, False),
    96: ("storm with hail", True, False),
    99: ("heavy storm with hail", True, False),
}


def _code_info(code) -> tuple:
    return _WEATHER_CODES.get(int(code or 0), ("unknown", False, False))


def _geocode(city: str):
    """Resolve a city name to (lat, lon, display_name)."""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    r = requests.get(
        url,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    if not d.get("results"):
        raise ValueError(f"city '{city}' not found")
    res = d["results"][0]
    return res.get("latitude"), res.get("longitude"), res.get("name")


def _fetch_forecast(lat: float, lon: float) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
            "precipitation,rain,snowfall,weather_code,wind_speed_10m"
        ),
        "hourly": "temperature_2m,precipitation_probability",
        "daily": (
            "temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,"
            "snowfall_sum,weather_code"
        ),
        "timezone": "auto",
        "forecast_days": 2,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_live_weather_data(city: str) -> dict:
    """Return a structured live-weather dict for Gemini to interpret.

    Raises ValueError on an unknown city; other network errors are surfaced so
    the caller can give a sensible message.
    """
    city = normalize_city(city)
    lat, lon, display = _geocode(city)
    data = _fetch_forecast(lat, lon)
    cur = data.get("current", {})
    daily = data.get("daily", {})
    code, rain_now, snow_now = _code_info(cur.get("weather_code"))
    t = cur.get("temperature_2m")
    notes = []
    if t is not None:
        if t >= 30:
            notes.append("very hot")
        elif t >= 25:
            notes.append("hot")
        elif t >= 15:
            notes.append("mild")
        elif t >= 5:
            notes.append("cool")
        else:
            notes.append("cold")
    return {
        "city": display,
        "current": {
            "temperature_c": t,
            "apparent_temperature_c": cur.get("apparent_temperature"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "conditions": code,
            "raining_now": rain_now,
            "snowing_now": snow_now,
            "precipitation_mm": cur.get("precipitation"),
            "is_day": bool(cur.get("is_day")),
            "note": notes,
        },
        "today": {
            "max_c": daily.get("temperature_2m_max", [None] * 2)[0],
            "min_c": daily.get("temperature_2m_min", [None] * 2)[0],
            "precip_mm": daily.get("precipitation_sum", [None] * 2)[0],
            "rain_mm": daily.get("rain_sum", [None] * 2)[0],
            "conditions": daily.get("weather_code", [None] * 2)[0],
        },
        "tomorrow": {
            "max_c": daily.get("temperature_2m_max", [None] * 2)[1],
            "min_c": daily.get("temperature_2m_min", [None] * 2)[1],
            "precip_mm": daily.get("precipitation_sum", [None] * 2)[1],
            "conditions": daily.get("weather_code", [None] * 2)[1],
        },
    }


# Time words (Turkish + English) recognized in "weather ..." requests, mapped
# to an offset in the daily forecast (0 = today, 1 = tomorrow).
_TIME_WORDS = {
    "bugun": 0,
    "bugün": 0,
    "today": 0,
    "su an": 0,
    "şu an": 0,
    "simdi": 0,
    "şimdi": 0,
    "now": 0,
    "yarin": 1,
    "yarın": 1,
    "tomorrow": 1,
    "tonight": 1,
    "gece": 1,
}

_DAY_LABELS = {0: "Today", 1: "Tomorrow"}


def resolve_day(when) -> int:
    """Map a time word to a forecast offset. Unknown/None -> 0 (today)."""
    if when is None:
        return 0
    low = str(when).strip().lower().replace("  ", " ")
    if low in _TIME_WORDS:
        return _TIME_WORDS[low]
    for phrase, off in _TIME_WORDS.items():
        if f" {phrase} " in f" {low} ":
            return off
    return 0


def _daily_day(data: dict, offset: int) -> dict:
    key = "today" if offset == 0 else "tomorrow"
    return data.get(key, {})


def get_weather_summary(city: str, when=None) -> str:
    """Weather summary for a specific day (when -> today/tomorrow/...)."""
    data = get_live_weather_data(city)
    offset = resolve_day(when)
    day = _daily_day(data, offset)
    is_future = offset > 0
    label = _DAY_LABELS.get(offset, "Target day")

    def cond(code):
        return _code_info(code)[0]

    if is_future:
        lines = [f"Weather forecast for {data['city']} ({label}):"]
        lines.append(f"- {label}: low {day.get('min_c') or '?'}°C, high {day.get('max_c') or '?'}°C")
        c = cond(day.get("conditions"))
        lines.append(f"- Conditions: {c}")
        precip = day.get("precip_mm")
        if precip:
            lines.append(f"- Precipitation: {precip:.0f} mm")
        if precip and precip > 2:
            lines.append(f"- Advice: {label} rain is expected, take an umbrella.")
        else:
            lines.append(f"- Advice: no rain expected {label}, go out comfortably.")
        return "\n".join(lines)

    cur = data["current"]
    today = data["today"]

    lines = [f"Current weather for {data['city']}:"]
    t = cur.get("temperature_c")
    app = cur.get("apparent_temperature_c")
    if t is not None:
        lines.append(f"- Temperature: {t:.0f}°C (feels like {app:.0f}°C)")
    if cur.get("humidity_pct") is not None:
        lines.append(f"- Humidity: %{cur['humidity_pct']:.0f}")
    if cur.get("wind_kmh") is not None:
        lines.append(f"- Wind: {cur['wind_kmh']:.0f} km/h")
    lines.append(f"- Conditions: {cur['conditions']}")
    if today.get("max_c") is not None:
        lines.append(f"- Today: low {today['min_c']:.0f}°C, high {today['max_c']:.0f}°C")

    # Advice.
    reasons = []
    if cur.get("raining_now"):
        reasons.append("it is raining now")
    elif cur.get("precipitation_mm") and cur["precipitation_mm"] > 0:
        reasons.append("there is precipitation")
    if today.get("precip_mm") and today["precip_mm"] > 2:
        reasons.append("rain is expected during the day")
    if t is not None and t >= 33:
        reasons.append("it is very hot")
    elif t is not None and t <= 0:
        reasons.append("it is very cold")
    wind = cur.get("wind_kmh")
    if wind is not None and wind >= 40:
        reasons.append("it is very windy")

    if reasons:
        advice = "so going out is not recommended"
        extras = []
        if any(r in ("it is raining now", "there is precipitation", "rain is expected during the day") for r in reasons):
            extras.append("take an umbrella")
        if any("hot" in r for r in reasons):
            extras.append("wear cool clothes, carry water, avoid the midday sun")
        if any("cold" in r for r in reasons):
            extras.append("wear warm clothes")
        if any("wind" in r for r in reasons):
            extras.append("dress against the wind")
        tail = (" — " + ", ".join(extras)) if extras else ""
        lines.append(f"- Advice: {', '.join(reasons)}; {advice}{tail}.")
    else:
        lines.append("- Advice: weather is fine, feel free to go out.")
    return "\n".join(lines)
