"""No-key weather tool backed by Open-Meteo."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List

from handlers.agent.tools.base_tool import BaseTool, ToolResult


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherTool(BaseTool):
    """Query current weather and daily forecast without an API key."""

    def __init__(self, *, timeout: float = 8.0, urlopen: Callable | None = None):
        self._timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen

    @property
    def name(self) -> str:
        return "get_weather"

    @property
    def category(self) -> str:
        return "weather"

    @property
    def requires(self) -> list[str]:
        return ["network"]

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def description(self) -> str:
        return (
            "查询指定城市或地区的当前天气和未来天气预报。"
            "当用户询问天气、温度、下雨、刮风、出门穿衣建议等内容时使用。"
            "无需 API Key，默认使用 Open-Meteo 数据。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "城市或地区名称，如 北京、上海、成都、Tokyo。",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 7,
                    "description": "预报天数，1 到 7，默认 1。",
                },
            },
            "required": ["location"],
        }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        location_query = str(args.get("location", "")).strip()
        if not location_query:
            return ToolResult(success=False, error="location is required")

        days = _coerce_int(args.get("days"), 1)
        days = min(max(days, 1), 7)

        try:
            location = self._geocode(location_query)
            forecast = self._forecast(location, days)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        return ToolResult(
            success=True,
            data={
                "provider": "open-meteo",
                "query": location_query,
                "location": location,
                "current": _parse_current(forecast.get("current") or {}),
                "daily_forecast": _parse_daily(forecast.get("daily") or {}),
            },
        )

    def _geocode(self, location: str) -> Dict[str, Any]:
        payload = _get_json(
            self._urlopen,
            _GEOCODING_URL,
            {
                "name": location,
                "count": 1,
                "language": "zh",
                "format": "json",
            },
            timeout=self._timeout,
        )
        results = payload.get("results") or []
        if not results:
            raise RuntimeError(f"未找到地点：{location}")
        item = results[0]
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        if latitude is None or longitude is None:
            raise RuntimeError(f"地点缺少经纬度：{location}")
        return {
            "name": item.get("name") or location,
            "country": item.get("country") or "",
            "admin1": item.get("admin1") or "",
            "latitude": latitude,
            "longitude": longitude,
            "timezone": item.get("timezone") or "auto",
        }

    def _forecast(self, location: Dict[str, Any], days: int) -> Dict[str, Any]:
        return _get_json(
            self._urlopen,
            _FORECAST_URL,
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": ",".join([
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "rain",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_direction_10m",
                ]),
                "daily": ",".join([
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                ]),
                "timezone": location.get("timezone") or "auto",
                "forecast_days": days,
            },
            timeout=self._timeout,
        )


def _get_json(
    urlopen: Callable,
    base_url: str,
    params: Dict[str, Any],
    *,
    timeout: float,
) -> Dict[str, Any]:
    url = base_url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "OpenAvatarChat/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except TimeoutError as e:
        raise RuntimeError("weather request timed out") from e
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"weather request HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"weather request failed: {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"weather service returned invalid JSON: {e}") from e


def _parse_current(current: Dict[str, Any]) -> Dict[str, Any]:
    weather_code = _coerce_int(current.get("weather_code"), -1)
    return {
        "time": current.get("time") or "",
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "relative_humidity_percent": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "rain_mm": current.get("rain"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "wind_direction_degrees": current.get("wind_direction_10m"),
        "weather_code": weather_code,
        "weather_description": _weather_description(weather_code),
    }


def _parse_daily(daily: Dict[str, Any]) -> List[Dict[str, Any]]:
    dates = daily.get("time") or []
    result = []
    for idx, date in enumerate(dates):
        weather_code = _coerce_int(_get_index(daily.get("weather_code"), idx), -1)
        result.append({
            "date": date,
            "weather_code": weather_code,
            "weather_description": _weather_description(weather_code),
            "temperature_max_c": _get_index(daily.get("temperature_2m_max"), idx),
            "temperature_min_c": _get_index(daily.get("temperature_2m_min"), idx),
            "precipitation_sum_mm": _get_index(daily.get("precipitation_sum"), idx),
            "precipitation_probability_max_percent": _get_index(
                daily.get("precipitation_probability_max"), idx
            ),
        })
    return result


def _get_index(values: Any, idx: int) -> Any:
    if isinstance(values, list) and idx < len(values):
        return values[idx]
    return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _weather_description(code: int) -> str:
    descriptions = {
        0: "晴",
        1: "晴间多云",
        2: "多云",
        3: "阴",
        45: "雾",
        48: "雾凇",
        51: "小毛毛雨",
        53: "中等毛毛雨",
        55: "大毛毛雨",
        56: "冻毛毛雨",
        57: "强冻毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "冻雨",
        67: "强冻雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "小阵雨",
        81: "中等阵雨",
        82: "强阵雨",
        85: "小阵雪",
        86: "强阵雪",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴大冰雹",
    }
    return descriptions.get(code, "未知天气")


def register_tools(registry, **_kwargs) -> None:
    registry.register(WeatherTool())
