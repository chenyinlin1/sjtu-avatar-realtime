from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.v1_adapter.asr.recognizer import SpeechRecognitionError, SpeechRecognitionTimeout
from service.v1_adapter.asr.schemas import ASRTranscribeRequest
from service.v1_adapter.asr.service import ASRService
from service.v1_adapter.responses import V1HTTPException
from service.v1_adapter.router import register_v1_adapter


class _FakeRecognizer:
    def __init__(self, _config, result="测试文本", error=None):
        self.result = result
        self.error = error

    def transcribe(self, _audio_bytes):
        if self.error:
            raise self.error
        return self.result


def _payload(audio_bytes: bytes = b"\x00\x00") -> dict:
    return {"audio_base64": base64.b64encode(audio_bytes).decode("ascii")}


def test_asr_route_returns_v1_response(monkeypatch):
    monkeypatch.setenv("DEVICE_SECRET_KEY", "device-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    app = FastAPI()
    register_v1_adapter(app)

    from service.v1_adapter.asr import routes

    monkeypatch.setattr(
        routes._service,
        "recognizer_factory",
        lambda config: _FakeRecognizer(config),
    )
    response = TestClient(app).post(
        "/api/v1/asr/transcribe",
        headers={"secretKey": "device-secret"},
        json=_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["text"] == "测试文本"
    assert body["data"]["sample_rate"] == 16000


def test_asr_route_requires_device_secret(monkeypatch):
    monkeypatch.setenv("DEVICE_SECRET_KEY", "device-secret")
    app = FastAPI()
    register_v1_adapter(app)

    response = TestClient(app).post("/api/v1/asr/transcribe", json=_payload())

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_asr_service_rejects_invalid_base64():
    service = ASRService()
    payload = ASRTranscribeRequest(audio_base64="not-base64")

    with pytest.raises(V1HTTPException) as exc_info:
        service.transcribe(payload)

    assert exc_info.value.code == "INVALID_AUDIO"
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("upstream_error", "expected_code"),
    [
        (SpeechRecognitionError("failed"), "UPSTREAM_ERROR"),
        (SpeechRecognitionTimeout("late"), "UPSTREAM_TIMEOUT"),
    ],
)
def test_asr_service_maps_upstream_errors(monkeypatch, upstream_error, expected_code):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    service = ASRService(
        recognizer_factory=lambda config: _FakeRecognizer(config, error=upstream_error)
    )

    with pytest.raises(V1HTTPException) as exc_info:
        service.transcribe(ASRTranscribeRequest(**_payload()))

    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == 502
