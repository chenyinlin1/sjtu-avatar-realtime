import gzip
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "handlers"))

from handlers.agent.tools.tool_registry import ToolRegistry
from handlers.agent.tools.weather import WeatherTool, register_tools


class FakeResponse:
    def __init__(self, payload, *, headers=None, raw_body=None):
        self.payload = payload
        self.headers = headers or {}
        self.raw_body = raw_body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if self.raw_body is not None:
            return self.raw_body
        return json.dumps(self.payload).encode("utf-8")


def fake_gzip_response(payload):
    body = json.dumps(payload).encode("utf-8")
    return FakeResponse(payload, headers={"Content-Encoding": "gzip"}, raw_body=gzip.compress(body))


def fake_urlopen(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    if "geocoding-api.open-meteo.com" in parsed.netloc:
        assert params["name"] == ["北京"]
        return FakeResponse({
            "results": [{
                "name": "Beijing",
                "country": "China",
                "admin1": "Beijing",
                "latitude": 39.9075,
                "longitude": 116.3972,
                "timezone": "Asia/Shanghai",
            }]
        })

    if "api.open-meteo.com" in parsed.netloc:
        assert params["forecast_days"] == ["2"]
        return FakeResponse({
            "timezone": "Asia/Shanghai",
            "current": {
                "time": "2026-07-05T20:00",
                "temperature_2m": 28.4,
                "apparent_temperature": 30.1,
                "relative_humidity_2m": 62,
                "precipitation": 0.0,
                "rain": 0.0,
                "weather_code": 1,
                "wind_speed_10m": 9.2,
                "wind_direction_10m": 135,
            },
            "daily": {
                "time": ["2026-07-05", "2026-07-06"],
                "weather_code": [1, 61],
                "temperature_2m_max": [31.0, 29.0],
                "temperature_2m_min": [22.0, 21.0],
                "precipitation_sum": [0.0, 4.2],
                "precipitation_probability_max": [10, 70],
            },
        })

    raise AssertionError(f"unexpected URL: {url}")


def fake_qweather_urlopen(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    if "geoapi.qweather.com" in parsed.netloc:
        assert params["location"] == ["北京"]
        assert params["key"] == ["test-key"]
        return FakeResponse({
            "code": "200",
            "location": [{
                "id": "101010100",
                "name": "北京",
                "country": "中国",
                "adm1": "北京市",
                "adm2": "北京",
                "lat": "39.9042",
                "lon": "116.4074",
                "tz": "Asia/Shanghai",
            }],
        })

    if "devapi.qweather.com" in parsed.netloc and parsed.path.endswith("/now"):
        assert params["location"] == ["101010100"]
        assert params["key"] == ["test-key"]
        return FakeResponse({
            "code": "200",
            "now": {
                "obsTime": "2026-07-07T11:00+08:00",
                "temp": "29",
                "feelsLike": "31",
                "humidity": "68",
                "precip": "0.0",
                "wind360": "180",
                "windDir": "南风",
                "windScale": "2",
                "windSpeed": "9",
                "icon": "101",
                "text": "多云",
            },
        })

    if "devapi.qweather.com" in parsed.netloc and parsed.path.endswith("/3d"):
        assert params["location"] == ["101010100"]
        assert params["key"] == ["test-key"]
        return FakeResponse({
            "code": "200",
            "daily": [
                {
                    "fxDate": "2026-07-07",
                    "tempMax": "34",
                    "tempMin": "25",
                    "iconDay": "101",
                    "textDay": "多云",
                    "iconNight": "305",
                    "textNight": "小雨",
                    "precip": "1.2",
                    "humidity": "72",
                    "windDirDay": "南风",
                    "windScaleDay": "2",
                    "windSpeedDay": "9",
                },
                {
                    "fxDate": "2026-07-08",
                    "tempMax": "33",
                    "tempMin": "24",
                    "iconDay": "100",
                    "textDay": "晴",
                    "iconNight": "100",
                    "textNight": "晴",
                    "precip": "0.0",
                    "humidity": "61",
                    "windDirDay": "东南风",
                    "windScaleDay": "2",
                    "windSpeedDay": "8",
                },
            ],
        })

    raise AssertionError(f"unexpected URL: {url}")


def fake_qweather_host_urlopen(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    headers = {key.lower(): value for key, value in request.header_items()}

    assert parsed.netloc == "ma4ewv4ac4.re.qweatherapi.com"
    assert headers.get("x-qw-api-key") == "test-key"
    assert "authorization" not in headers
    assert "key" not in params

    if parsed.path.endswith("/geo/v2/city/lookup"):
        assert params["location"] == ["北京"]
        return fake_gzip_response({
            "code": "200",
            "location": [{
                "id": "101010100",
                "name": "北京",
                "country": "中国",
                "adm1": "北京市",
                "adm2": "北京",
                "lat": "39.9042",
                "lon": "116.4074",
                "tz": "Asia/Shanghai",
            }],
        })

    if parsed.path.endswith("/v7/weather/now"):
        assert params["location"] == ["101010100"]
        return FakeResponse({
            "code": "200",
            "now": {
                "obsTime": "2026-07-07T11:00+08:00",
                "temp": "29",
                "feelsLike": "31",
                "humidity": "68",
                "precip": "0.0",
                "wind360": "180",
                "windDir": "南风",
                "windScale": "2",
                "windSpeed": "9",
                "icon": "101",
                "text": "多云",
            },
        })

    if parsed.path.endswith("/v7/weather/3d"):
        assert params["location"] == ["101010100"]
        return FakeResponse({
            "code": "200",
            "daily": [{
                "fxDate": "2026-07-07",
                "tempMax": "34",
                "tempMin": "25",
                "iconDay": "101",
                "textDay": "多云",
                "iconNight": "305",
                "textNight": "小雨",
                "precip": "1.2",
                "humidity": "72",
                "windDirDay": "南风",
                "windScaleDay": "2",
                "windSpeedDay": "9",
            }],
        })

    raise AssertionError(f"unexpected URL: {url}")


def test_weather_tool_schema_is_openai_compatible():
    schema = WeatherTool(urlopen=fake_urlopen).get_openai_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_weather"
    assert schema["function"]["parameters"]["required"] == ["location"]
    assert "days" in schema["function"]["parameters"]["properties"]


def test_weather_tool_returns_current_and_daily_forecast(monkeypatch):
    monkeypatch.delenv("WEATHER_PROVIDER", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("HEWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_HOST", raising=False)

    result = WeatherTool(urlopen=fake_urlopen).execute({"location": "北京", "days": 2})

    assert result.success is True
    assert result.data["provider"] == "open-meteo"
    assert result.data["location"]["name"] == "Beijing"
    assert result.data["current"]["temperature_c"] == 28.4
    assert result.data["current"]["weather_description"] == "晴间多云"
    assert result.data["daily_forecast"][1]["weather_description"] == "小雨"


def test_weather_tool_can_use_qweather_provider(monkeypatch):
    monkeypatch.delenv("QWEATHER_API_HOST", raising=False)
    monkeypatch.delenv("QWEATHER_JWT_TOKEN", raising=False)
    monkeypatch.delenv("QWEATHER_BEARER_TOKEN", raising=False)

    result = WeatherTool(
        provider="qweather",
        qweather_api_key="test-key",
        urlopen=fake_qweather_urlopen,
    ).execute({"location": "北京", "days": 2})

    assert result.success is True
    assert result.data["provider"] == "qweather"
    assert result.data["location"]["id"] == "101010100"
    assert result.data["location"]["name"] == "北京"
    assert result.data["current"]["temperature_c"] == 29.0
    assert result.data["current"]["weather_description"] == "多云"
    assert result.data["daily_forecast"][0]["weather_description"] == "多云转小雨"
    assert result.data["daily_forecast"][1]["weather_description"] == "晴"


def test_weather_tool_uses_qweather_api_host_key_header(monkeypatch):
    monkeypatch.setenv("QWEATHER_API_HOST", "ma4ewv4ac4.re.qweatherapi.com")
    monkeypatch.delenv("QWEATHER_JWT_TOKEN", raising=False)
    monkeypatch.delenv("QWEATHER_BEARER_TOKEN", raising=False)

    result = WeatherTool(
        provider="qweather",
        qweather_api_key="test-key",
        urlopen=fake_qweather_host_urlopen,
    ).execute({"location": "北京", "days": 1})

    assert result.success is True
    assert result.data["provider"] == "qweather"
    assert result.data["location"]["id"] == "101010100"
    assert result.data["current"]["weather_description"] == "多云"


def test_weather_tool_registers_in_tool_registry():
    registry = ToolRegistry()
    register_tools(registry)

    assert "get_weather" in registry.tool_names
