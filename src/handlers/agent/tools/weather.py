"""Weather tool backed by QWeather or Open-Meteo fallback."""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from handlers.agent.tools.base_tool import BaseTool, ToolResult


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_QWEATHER_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"
_QWEATHER_WEATHER_URL = "https://devapi.qweather.com/v7/weather"


class WeatherTool(BaseTool):
    """Query current weather and daily forecast."""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        urlopen: Callable | None = None,
        provider: Optional[str] = None,
        qweather_api_key: Optional[str] = None,
    ):
        self._timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen
        self._provider = _normalize_provider(provider or os.getenv("WEATHER_PROVIDER") or "auto")
        self._qweather_api_key = _clean_api_key(qweather_api_key or os.getenv("QWEATHER_API_KEY") or os.getenv("HEWEATHER_API_KEY"))
        self._qweather_api_host = _normalize_qweather_host(os.getenv("QWEATHER_API_HOST"))
        self._qweather_token = _clean_api_key(os.getenv("QWEATHER_JWT_TOKEN") or os.getenv("QWEATHER_BEARER_TOKEN"))
        self._qweather_geo_url = os.getenv("QWEATHER_GEO_URL") or _QWEATHER_GEO_URL
        self._qweather_weather_url = os.getenv("QWEATHER_WEATHER_URL") or _QWEATHER_WEATHER_URL

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
            "优先使用和风天气 QWeather；没有 QWEATHER_API_KEY 时回退 Open-Meteo。"
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

        qweather_error = ""
        if self._should_use_qweather():
            try:
                return self._execute_qweather(location_query, days)
            except Exception as e:
                qweather_error = str(e)
                if not _env_bool("WEATHER_FALLBACK_OPEN_METEO", True):
                    return ToolResult(success=False, error=qweather_error)

        return self._execute_open_meteo(location_query, days, fallback_error=qweather_error)

    def _should_use_qweather(self) -> bool:
        if self._provider in {"qweather", "heweather", "hefeng"}:
            return True
        return self._provider == "auto" and bool(self._qweather_api_key)

    def _execute_open_meteo(
        self,
        location_query: str,
        days: int,
        *,
        fallback_error: str = "",
    ) -> ToolResult:
        try:
            location = self._geocode(location_query)
            forecast = self._forecast(location, days)
        except Exception as e:
            if fallback_error:
                return ToolResult(success=False, error=f"QWeather failed: {fallback_error}; Open-Meteo failed: {e}")
            return ToolResult(success=False, error=str(e))

        data = {
            "provider": "open-meteo",
            "query": location_query,
            "location": location,
            "current": _parse_current(forecast.get("current") or {}),
            "daily_forecast": _parse_daily(forecast.get("daily") or {}),
        }
        if fallback_error:
            data["fallback_from"] = "qweather"
            data["fallback_error"] = fallback_error

        return ToolResult(success=True, data=data)

    def _execute_qweather(self, location_query: str, days: int) -> ToolResult:
        if not self._qweather_api_key and not self._qweather_token:
            raise RuntimeError("QWEATHER_API_KEY or QWEATHER_JWT_TOKEN is not configured")

        location = self._qweather_geocode(location_query)
        now_payload = self._qweather_now(location["id"])
        daily_payload = self._qweather_daily(location["id"], days)
        return ToolResult(
            success=True,
            data={
                "provider": "qweather",
                "query": location_query,
                "location": location,
                "current": _parse_qweather_current(now_payload.get("now") or {}),
                "daily_forecast": _parse_qweather_daily(daily_payload.get("daily") or [], days),
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


    def _qweather_geocode(self, location: str) -> Dict[str, Any]:
        params = {
            "location": location,
            "number": 1,
            "lang": "zh",
        }
        headers = self._qweather_auth_headers()
        if not headers:
            params["key"] = self._qweather_api_key
        payload = _get_json(
            self._urlopen,
            self._qweather_url("geo/v2/city/lookup", self._qweather_geo_url),
            params,
            timeout=self._timeout,
            headers=headers,
        )
        _ensure_qweather_ok(payload, "city lookup")
        results = payload.get("location") or []
        if not results:
            raise RuntimeError(f"未找到地点：{location}")
        item = results[0]
        location_id = item.get("id")
        if not location_id:
            raise RuntimeError(f"地点缺少和风 location id：{location}")
        return {
            "id": location_id,
            "name": item.get("name") or location,
            "country": item.get("country") or "",
            "admin1": item.get("adm1") or "",
            "admin2": item.get("adm2") or "",
            "latitude": _coerce_float(item.get("lat")),
            "longitude": _coerce_float(item.get("lon")),
            "timezone": item.get("tz") or "Asia/Shanghai",
        }

    def _qweather_now(self, location_id: str) -> Dict[str, Any]:
        payload = self._qweather_get_weather("now", location_id)
        _ensure_qweather_ok(payload, "weather now")
        return payload

    def _qweather_daily(self, location_id: str, days: int) -> Dict[str, Any]:
        endpoint = "3d" if days <= 3 else "7d"
        payload = self._qweather_get_weather(endpoint, location_id)
        _ensure_qweather_ok(payload, f"weather {endpoint}")
        return payload

    def _qweather_get_weather(self, endpoint: str, location_id: str) -> Dict[str, Any]:
        params = {
            "location": location_id,
            "lang": "zh",
        }
        headers = self._qweather_auth_headers()
        if not headers:
            params["key"] = self._qweather_api_key
        return _get_json(
            self._urlopen,
            self._qweather_url(f"v7/weather/{endpoint}", f"{self._qweather_weather_url.rstrip('/')}/{endpoint}"),
            params,
            timeout=self._timeout,
            headers=headers,
        )

    def _qweather_url(self, path: str, legacy_url: str) -> str:
        if self._qweather_api_host:
            return f"{self._qweather_api_host}/{path.lstrip('/')}"
        return legacy_url

    def _qweather_auth_headers(self) -> Dict[str, str]:
        if self._qweather_api_host and self._qweather_token:
            return {"Authorization": f"Bearer {self._qweather_token}"}
        if self._qweather_api_host and self._qweather_api_key:
            return {"X-QW-Api-Key": self._qweather_api_key}
        return {}


def _get_json(
    urlopen: Callable,
    base_url: str,
    params: Dict[str, Any],
    *,
    timeout: float,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    url = base_url + "?" + urllib.parse.urlencode(params)
    request_headers = {"User-Agent": "OpenAvatarChat/1.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read()
            body = _decode_response_body(raw_body, response.headers.get("Content-Encoding", ""))
    except TimeoutError as e:
        raise RuntimeError("weather request timed out") from e
    except urllib.error.HTTPError as e:
        detail = _decode_response_body(e.read(), e.headers.get("Content-Encoding", ""))[:300]
        raise RuntimeError(f"weather request HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"weather request failed: {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"weather service returned invalid JSON: {e}") from e


def _decode_response_body(raw_body: bytes, content_encoding: str) -> str:
    if "gzip" in (content_encoding or "").lower() or raw_body.startswith(b"\x1f\x8b"):
        raw_body = gzip.decompress(raw_body)
    return raw_body.decode("utf-8", errors="replace")


def _ensure_qweather_ok(payload: Dict[str, Any], source: str) -> None:
    code = str(payload.get("code") or "")
    if code != "200":
        raise RuntimeError(f"QWeather {source} returned code {code or 'missing'}")


def _parse_qweather_current(now: Dict[str, Any]) -> Dict[str, Any]:
    weather_code = _coerce_int(now.get("icon"), -1)
    return {
        "time": now.get("obsTime") or "",
        "temperature_c": _coerce_float(now.get("temp")),
        "apparent_temperature_c": _coerce_float(now.get("feelsLike")),
        "relative_humidity_percent": _coerce_int(now.get("humidity"), -1),
        "precipitation_mm": _coerce_float(now.get("precip")),
        "rain_mm": _coerce_float(now.get("precip")),
        "wind_speed_kmh": _coerce_float(now.get("windSpeed")),
        "wind_direction_degrees": _coerce_int(now.get("wind360"), -1),
        "wind_direction": now.get("windDir") or "",
        "wind_scale": now.get("windScale") or "",
        "weather_code": weather_code,
        "weather_description": now.get("text") or _weather_description(weather_code),
    }


def _parse_qweather_daily(daily: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in daily[:days]:
        day_text = item.get("textDay") or ""
        night_text = item.get("textNight") or ""
        if day_text and night_text and day_text != night_text:
            weather_description = f"{day_text}转{night_text}"
        else:
            weather_description = day_text or night_text or "未知天气"

        result.append({
            "date": item.get("fxDate") or "",
            "weather_code": _coerce_int(item.get("iconDay") or item.get("iconNight"), -1),
            "weather_description": weather_description,
            "temperature_max_c": _coerce_float(item.get("tempMax")),
            "temperature_min_c": _coerce_float(item.get("tempMin")),
            "precipitation_sum_mm": _coerce_float(item.get("precip")),
            "precipitation_probability_max_percent": _coerce_int(item.get("precipProb"), -1),
            "humidity_percent": _coerce_int(item.get("humidity"), -1),
            "wind_direction_day": item.get("windDirDay") or "",
            "wind_scale_day": item.get("windScaleDay") or "",
            "wind_speed_day_kmh": _coerce_float(item.get("windSpeedDay")),
        })
    return result


def _normalize_provider(value: str) -> str:
    provider = (value or "auto").strip().lower().replace("_", "-")
    if provider in {"openmeteo", "open-meteo"}:
        return "open-meteo"
    if provider in {"qweather", "heweather", "hefeng"}:
        return provider
    return "auto"


def _clean_api_key(value: str | None) -> str:
    if not value:
        return ""
    return str(value).strip().strip("'\"‘’“” ")


def _normalize_qweather_host(value: str | None) -> str:
    host = _clean_api_key(value).rstrip("/")
    if not host:
        return ""
    if host.startswith(("http://", "https://")):
        return host
    return f"https://{host}"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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




def _coerce_float(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
