from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from handlers.client.rtc_client import client_handler_rtc as rtc_module
from handlers.client.rtc_client.client_handler_rtc import ClientHandlerRtc


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=(30, 120, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_rtc_client_detects_active_session_delegate():
    handler = ClientHandlerRtc()

    assert handler._has_active_sessions() is False

    handler.handler_delegate.session_delegates["session-a"] = object()

    assert handler._has_active_sessions() is True


def test_rtc_client_finds_flashhead_handler_by_update_capability():
    flashhead_handler = SimpleNamespace(update_condition_image=lambda image_path: image_path)
    registry = SimpleNamespace(handler=flashhead_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )

    handler = ClientHandlerRtc()
    handler.handler_delegate.engine_ref = lambda: engine

    assert handler._find_flashhead_handler() is flashhead_handler


def test_rtc_init_config_advertises_avatar_clone_route_when_flashhead_is_enabled():
    flashhead_handler = SimpleNamespace(update_condition_image=lambda image_path: image_path)
    registry = SimpleNamespace(handler=flashhead_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )

    handler = ClientHandlerRtc()
    handler.handler_delegate.engine_ref = lambda: engine

    config = handler.build_frontend_init_config(avatar_config={}, rtc_configuration=None)

    assert config["avatar_clone"] == {
        "enabled": True,
        "upload_route": "/openavatarchat/avatar/flashhead/image",
    }


def test_rtc_init_config_advertises_voice_clone_route_when_tts_supports_voice_clone():
    tts_handler = SimpleNamespace(
        update_voice_clone=lambda voice_id, model_name=None: None,
        get_voice_clone_target_model=lambda: "cosyvoice-v3-flash",
        get_voice_clone_status=lambda: {
            "active": False,
            "model_name": "cosyvoice-v3-flash",
        },
    )
    registry = SimpleNamespace(handler=tts_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )

    handler = ClientHandlerRtc()
    handler.handler_delegate.engine_ref = lambda: engine

    config = handler.build_frontend_init_config(avatar_config={}, rtc_configuration=None)

    assert config["voice_clone"]["enabled"] is True
    assert config["voice_clone"]["upload_route"] == "/openavatarchat/voice-clone"
    assert config["voice_clone"]["reset_route"] == "/openavatarchat/voice-clone/reset"
    assert "sample_text" in config["voice_clone"]


def test_flashhead_avatar_upload_route_rejects_active_conversation():
    app = FastAPI()
    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.session_delegates["session-a"] = object()
    handler.register_flashhead_avatar_upload_route(app)

    response = TestClient(app).post(
        "/openavatarchat/avatar/flashhead/image",
        files={"file": ("person.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 409
    assert "stop" in response.json()["detail"].lower()


def test_flashhead_avatar_upload_route_updates_flashhead_handler(monkeypatch):
    app = FastAPI()
    updated_paths = []
    flashhead_handler = SimpleNamespace(update_condition_image=updated_paths.append)
    registry = SimpleNamespace(handler=flashhead_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )
    upload_result = SimpleNamespace(
        absolute_path="/tmp/person.png",
        to_response=lambda: {
            "status": "ok",
            "avatar_image_path": "resource/avatar/flashhead/uploads/person.png",
            "filename": "person.png",
        },
    )

    def fake_save_avatar_image_bytes(*, data, original_filename, content_type):
        assert data
        assert original_filename == "person.png"
        assert content_type == "image/png"
        return upload_result

    monkeypatch.setattr(rtc_module, "save_avatar_image_bytes", fake_save_avatar_image_bytes)

    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.engine_ref = lambda: engine
    handler.register_flashhead_avatar_upload_route(app)

    response = TestClient(app).post(
        "/openavatarchat/avatar/flashhead/image",
        files={"file": ("person.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert updated_paths == ["/tmp/person.png"]


def test_voice_clone_upload_route_creates_voice_and_updates_tts(monkeypatch):
    app = FastAPI()
    updated_voice = []
    tts_handler = SimpleNamespace(
        update_voice_clone=lambda voice_id, model_name=None: updated_voice.append((voice_id, model_name)),
        get_voice_clone_target_model=lambda: "cosyvoice-v3-flash",
        get_voice_clone_status=lambda: {"active": False, "model_name": "cosyvoice-v3-flash"},
    )
    registry = SimpleNamespace(handler=tts_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )
    upload_result = SimpleNamespace(
        filename="voice.wav",
        to_response=lambda voice_id, model_name: {
            "status": "ok",
            "voice_id": voice_id,
            "model_name": model_name,
            "filename": "voice.wav",
        },
    )

    def fake_save_voice_clone_audio_bytes(*, data, original_filename, content_type):
        assert data == b"audio"
        assert original_filename == "voice.webm"
        assert content_type == "audio/webm"
        return upload_result

    def fake_create_cosyvoice_voice_clone(*, audio_url, target_model, api_key):
        assert audio_url == "http://testserver/openavatarchat/voice-clone/audio/voice.wav"
        assert target_model == "cosyvoice-v3-flash"
        return "voice_id_123"

    monkeypatch.setattr(rtc_module, "save_voice_clone_audio_bytes", fake_save_voice_clone_audio_bytes)
    monkeypatch.setattr(rtc_module, "create_cosyvoice_voice_clone", fake_create_cosyvoice_voice_clone)

    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.engine_ref = lambda: engine
    handler.register_voice_clone_routes(app)

    response = TestClient(app).post(
        "/openavatarchat/voice-clone",
        files={"file": ("voice.webm", b"audio", "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json()["voice_id"] == "voice_id_123"
    assert updated_voice == [("voice_id_123", "cosyvoice-v3-flash")]


def test_voice_clone_upload_uses_configured_public_url_for_bailian_download(monkeypatch):
    app = FastAPI()
    created_voice = {}
    tts_handler = SimpleNamespace(
        update_voice_clone=lambda voice_id, model_name=None: None,
        get_voice_clone_target_model=lambda: "cosyvoice-v3-flash",
        get_voice_clone_status=lambda: {"active": False, "model_name": "cosyvoice-v3-flash"},
    )
    registry = SimpleNamespace(handler=tts_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )
    upload_result = SimpleNamespace(
        filename="voice.wav",
        to_response=lambda voice_id, model_name: {
            "status": "ok",
            "voice_id": voice_id,
            "model_name": model_name,
        },
    )

    def fake_save_voice_clone_audio_bytes(*, data, original_filename, content_type):
        return upload_result

    def fake_create_cosyvoice_voice_clone(*, audio_url, target_model, api_key):
        created_voice["audio_url"] = audio_url
        return "voice_id_123"

    monkeypatch.setenv(
        "OPENAVATARCHAT_PUBLIC_URL",
        "https://u848390-a11e-6f8e472d.cqa1.seetacloud.com:8443/",
    )
    monkeypatch.setattr(rtc_module, "save_voice_clone_audio_bytes", fake_save_voice_clone_audio_bytes)
    monkeypatch.setattr(rtc_module, "create_cosyvoice_voice_clone", fake_create_cosyvoice_voice_clone)

    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.engine_ref = lambda: engine
    handler.register_voice_clone_routes(app)

    response = TestClient(app).post(
        "/openavatarchat/voice-clone",
        files={"file": ("voice.webm", b"audio", "audio/webm")},
        headers={"host": "127.0.0.1:6006"},
    )

    assert response.status_code == 200
    assert created_voice["audio_url"] == (
        "https://u848390-a11e-6f8e472d.cqa1.seetacloud.com:8443"
        "/openavatarchat/voice-clone/audio/voice.wav"
    )


def test_voice_clone_upload_reports_bailian_audio_download_failure(monkeypatch):
    app = FastAPI()
    tts_handler = SimpleNamespace(
        update_voice_clone=lambda voice_id, model_name=None: None,
        get_voice_clone_target_model=lambda: "cosyvoice-v3-flash",
        get_voice_clone_status=lambda: {"active": False, "model_name": "cosyvoice-v3-flash"},
    )
    registry = SimpleNamespace(handler=tts_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )
    upload_result = SimpleNamespace(filename="voice.wav")

    def fake_save_voice_clone_audio_bytes(*, data, original_filename, content_type):
        return upload_result

    def fake_create_cosyvoice_voice_clone(*, audio_url, target_model, api_key):
        raise RuntimeError("BadRequest.InputDownloadFailed: download audio failed")

    monkeypatch.setattr(rtc_module, "save_voice_clone_audio_bytes", fake_save_voice_clone_audio_bytes)
    monkeypatch.setattr(rtc_module, "create_cosyvoice_voice_clone", fake_create_cosyvoice_voice_clone)

    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.engine_ref = lambda: engine
    handler.register_voice_clone_routes(app)

    response = TestClient(app).post(
        "/openavatarchat/voice-clone",
        files={"file": ("voice.webm", b"audio", "audio/webm")},
    )

    assert response.status_code == 502
    assert "Bailian could not download" in response.json()["detail"]


def test_voice_clone_reset_route_restores_default_voice():
    app = FastAPI()
    reset_calls = []
    tts_handler = SimpleNamespace(
        update_voice_clone=lambda voice_id, model_name=None: None,
        get_voice_clone_target_model=lambda: "cosyvoice-v3-flash",
        reset_voice_clone=lambda: reset_calls.append(True),
        get_voice_clone_status=lambda: {"active": False, "voice_id": None},
    )
    registry = SimpleNamespace(handler=tts_handler)
    engine = SimpleNamespace(
        handler_manager=SimpleNamespace(
            get_enabled_handler_registries=lambda order_by_priority=True: [registry]
        )
    )

    handler = ClientHandlerRtc()
    handler.rtc_streamer_factory = SimpleNamespace(streams={})
    handler.handler_delegate.engine_ref = lambda: engine
    handler.register_voice_clone_routes(app)

    response = TestClient(app).post("/openavatarchat/voice-clone/reset")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert reset_calls == [True]
