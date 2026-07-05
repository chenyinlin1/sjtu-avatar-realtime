from __future__ import annotations

import io
import wave
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from service.frontend_service import web_persona_routes as web_routes
from service.v1_adapter.personas.media_storage import PersonaMediaStorage
from service.v1_adapter.personas.repository import PersonaRepository
from service.v1_adapter.personas.service import PersonaService
from service.v1_adapter.responses import V1HTTPException, v1_http_exception_handler


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (220, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _wav_bytes(sample_rate: int = 16000, seconds: float = 0.1) -> bytes:
    frames = b"\x00\x00" * int(sample_rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return buffer.getvalue()


def _client(tmp_path) -> tuple[TestClient, PersonaService]:
    service = PersonaService(
        repository=PersonaRepository(tmp_path / "personas"),
        media_storage=PersonaMediaStorage(tmp_path / "personas"),
    )
    app = FastAPI()
    app.add_exception_handler(V1HTTPException, v1_http_exception_handler)
    web_routes.register_web_persona_routes(app, service=service)
    return TestClient(app), service


def test_web_persona_routes_create_multiple_personas_and_upload_assets(tmp_path, monkeypatch):
    client, service = _client(tmp_path)

    first = client.post(
        "/openavatarchat/personas",
        json={
            "display_name": "儿子小明",
            "elder_id": "elder_1",
            "tenant_id": "tenant_1",
            "relationship": "儿子",
            "self_reference": "小明",
            "is_default": True,
        },
    )
    assert first.status_code == 200
    first_persona_id = first.json()["persona_id"]

    second = client.post(
        "/openavatarchat/personas",
        json={
            "display_name": "女儿小丽",
            "elder_id": "elder_1",
            "tenant_id": "tenant_1",
            "relationship": "女儿",
            "self_reference": "小丽",
        },
    )
    assert second.status_code == 200
    second_persona_id = second.json()["persona_id"]
    assert second_persona_id != first_persona_id

    listed = client.get("/openavatarchat/personas?elder_id=elder_1&tenant_id=tenant_1")
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert listed_payload["default_persona_id"] == first_persona_id
    assert {item["persona_id"] for item in listed_payload["items"]} == {
        first_persona_id,
        second_persona_id,
    }

    def save_avatar_in_tmp(**kwargs):
        return web_routes.save_avatar_image_bytes.__wrapped__(  # type: ignore[attr-defined]
            **kwargs,
            project_root=tmp_path,
            upload_dir=tmp_path / "avatar_uploads",
        )

    original_save_avatar = web_routes.save_avatar_image_bytes
    save_avatar_in_tmp.__wrapped__ = original_save_avatar  # type: ignore[attr-defined]
    monkeypatch.setattr(web_routes, "save_avatar_image_bytes", save_avatar_in_tmp)

    face = client.post(
        f"/openavatarchat/personas/{first_persona_id}/face",
        files={"file": ("face.png", _png_bytes(), "image/png")},
    )
    assert face.status_code == 200
    assert face.json()["face_status"] == "READY"
    assert service.get_persona(first_persona_id)["face"]["status"] == "READY"
    assert service.get_persona(second_persona_id)["face"]["status"] == "NONE"

    voice_sample = tmp_path / "voice_sample.wav"
    voice_sample.write_bytes(_wav_bytes())

    def fake_save_voice_clone_audio_bytes(**_kwargs):
        return SimpleNamespace(
            absolute_path=str(voice_sample),
            filename="persona_voice.wav",
            duration_seconds=6.2,
        )

    captured_voice_clone = {}

    def fake_create_voice_clone(**kwargs):
        captured_voice_clone.update(kwargs)
        return "voice_web_123"

    monkeypatch.setattr(web_routes, "save_voice_clone_audio_bytes", fake_save_voice_clone_audio_bytes)
    monkeypatch.setattr(web_routes, "create_cosyvoice_voice_clone", fake_create_voice_clone)

    voice = client.post(
        f"/openavatarchat/personas/{first_persona_id}/voice",
        headers={"origin": "https://avatar.example.test"},
        data={"ref_text": "妈，我是小明。", "source_duration_ms": "6200"},
        files={"file": ("voice.webm", b"fake webm", "audio/webm")},
    )
    assert voice.status_code == 200
    voice_payload = voice.json()
    assert voice_payload["voice_status"] == "READY"
    assert voice_payload["voice_id"] == "voice_web_123"
    assert captured_voice_clone["audio_url"] == (
        "https://avatar.example.test/openavatarchat/personas/voice-clone/audio/persona_voice.wav"
    )
    assert captured_voice_clone["target_model"] == "cosyvoice-v3-flash"

    first_persona = service.get_persona(first_persona_id)
    second_persona = service.get_persona(second_persona_id)
    assert first_persona["voice"]["status"] == "READY"
    assert first_persona["voice"]["voice_id"] == "voice_web_123"
    assert first_persona["voice"]["sample_duration_ms"] == 6200
    assert second_persona["voice"]["status"] == "NONE"
