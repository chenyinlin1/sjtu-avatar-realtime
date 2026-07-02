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
