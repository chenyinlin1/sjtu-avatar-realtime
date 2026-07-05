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
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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


def test_weather_tool_schema_is_openai_compatible():
    schema = WeatherTool(urlopen=fake_urlopen).get_openai_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_weather"
    assert schema["function"]["parameters"]["required"] == ["location"]
    assert "days" in schema["function"]["parameters"]["properties"]


def test_weather_tool_returns_current_and_daily_forecast():
    result = WeatherTool(urlopen=fake_urlopen).execute({"location": "北京", "days": 2})

    assert result.success is True
    assert result.data["provider"] == "open-meteo"
    assert result.data["location"]["name"] == "Beijing"
    assert result.data["current"]["temperature_c"] == 28.4
    assert result.data["current"]["weather_description"] == "晴间多云"
    assert result.data["daily_forecast"][1]["weather_description"] == "小雨"


def test_weather_tool_registers_in_tool_registry():
    registry = ToolRegistry()
    register_tools(registry)

    assert "get_weather" in registry.tool_names
