from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace

import numpy as np

from chat_engine.contexts.session_context import SharedStates
from service.rtc_service.rtc_stream import RtcStream
from service.v1_adapter.personas.media_storage import PersonaMediaStorage
from service.v1_adapter.personas.repository import PersonaRepository
from service.v1_adapter.personas.runtime import PersonaRuntimeResolver
from service.v1_adapter.personas.schemas import PersonaUpsertRequest
from service.v1_adapter.personas.service import PersonaService
from handlers.tts.bailian_tts import tts_handler_cosyvoice_bailian as tts_module
from handlers.tts.bailian_tts.tts_handler_cosyvoice_bailian import BailianTTSSession, TTSContext
from handlers.avatar.flashhead.avatar_handler_flashhead import HandlerAvatarFlashHead


def _wav_bytes(sample_rate: int = 16000, seconds: float = 0.1) -> bytes:
    samples = np.zeros(int(sample_rate * seconds), dtype=np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())
    return buffer.getvalue()


def _service(tmp_path) -> tuple[PersonaService, PersonaRepository]:
    repository = PersonaRepository(tmp_path)
    media_storage = PersonaMediaStorage(tmp_path)
    return PersonaService(repository=repository, media_storage=media_storage), repository


def test_persona_runtime_resolves_metadata_voice_and_face(tmp_path, monkeypatch):
    service, repository = _service(tmp_path)
    service.upsert_persona(
        "p_son",
        PersonaUpsertRequest(
            elder_id="elder_1",
            tenant_id="tenant_1",
            relationship="SON",
            display_name="儿子·王小明",
            address_to_elder="妈",
            self_reference="小明",
            gender="MALE",
            persona_prompt="说话慢一点，多关心身体。",
            is_default=True,
        ),
    )

    monkeypatch.setattr(service.media_storage, "download", lambda url, max_bytes: (_wav_bytes(), "voice.wav"))
    monkeypatch.setattr(service, "_create_voice_clone", lambda **kwargs: "voice_real_123")

    service.upload_voice_url(
        "p_son",
        audio_url="https://files.example.com/voice.wav",
        ref_text="妈，我是小明。",
        source_duration_ms=100,
    )
    service.upload_face_bytes("p_son", "face.png", b"png")

    runtime = PersonaRuntimeResolver(repository).resolve(
        persona_id="p_son",
        elder_id="elder_1",
        tenant_id="tenant_1",
    )

    assert runtime["voice_id"] == "voice_real_123"
    assert runtime["voice_model_name"] == "cosyvoice-v3-flash"
    assert runtime["face_image_path"].endswith("face.png")
    assert "王小明" in runtime["persona_system_prompt"]
    assert "妈" in runtime["persona_system_prompt"]


def test_device_info_sets_session_persona_runtime(tmp_path, monkeypatch):
    service, _repository = _service(tmp_path)
    service.upsert_persona(
        "p_default",
        PersonaUpsertRequest(
            elder_id="elder_1",
            tenant_id="tenant_1",
            display_name="女儿·王丽",
            is_default=True,
        ),
    )
    monkeypatch.setenv("V1_PERSONA_STORAGE_ROOT", str(tmp_path))

    sent = []
    stream = RtcStream(session_id="s1")
    stream.chat_channel = SimpleNamespace(send=lambda raw: sent.append(json.loads(raw)))
    stream.client_session_delegate = SimpleNamespace(shared_states=SharedStates())

    stream._handle_device_info(
        {"device_sn": "speaker_1", "elder_id": "elder_1", "tenant_id": "tenant_1"},
        "req_1",
    )

    assert stream.client_session_delegate.device_info["device_sn"] == "speaker_1"
    runtime = stream.client_session_delegate.shared_states.persona_runtime
    assert runtime["persona_id"] == "p_default"
    assert sent[-1]["header"]["name"] == "DeviceInfoAck"
    assert sent[-1]["payload"]["persona_active"] is True


def test_tts_context_uses_persona_voice(monkeypatch):
    created = {}

    class FakeSynthesizer:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(tts_module, "SpeechSynthesizer", FakeSynthesizer)

    context = TTSContext("session_1")
    context.shared_states = SimpleNamespace(
        persona_runtime={
            "persona_id": "p_voice",
            "voice_id": "voice_persona",
            "voice_model_name": "cosyvoice-v3-flash",
        }
    )
    context.data_submitter = SimpleNamespace(
        get_streamer=lambda data_type: SimpleNamespace(data_definition=object())
    )
    session = BailianTTSSession(input_stream_id=SimpleNamespace(key="input-key"))
    handler = SimpleNamespace(model_name="default-model", voice="default-voice", instruction=None)

    context._ensure_synthesizer(session, handler)

    assert created["voice"] == "voice_persona"
    assert created["model"] == "cosyvoice-v3-flash"


def test_tts_context_falls_back_v35_default_voice_to_v3_flash(monkeypatch):
    created = {}

    class FakeSynthesizer:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(tts_module, "SpeechSynthesizer", FakeSynthesizer)

    context = TTSContext("session_1")
    context.shared_states = SimpleNamespace()
    context.data_submitter = SimpleNamespace(
        get_streamer=lambda data_type: SimpleNamespace(data_definition=object())
    )
    session = BailianTTSSession(input_stream_id=SimpleNamespace(key="input-key"))
    handler = SimpleNamespace(
        model_name="cosyvoice-v3.5-flash",
        voice="longanhuan_v3",
        instruction="请用四川话表达。",
    )

    context._ensure_synthesizer(session, handler)

    assert created["voice"] == "longanhuan_v3"
    assert created["model"] == "cosyvoice-v3-flash"
    assert created["instruction"] == "请用四川话表达。"
    assert session.model_name == "cosyvoice-v3-flash"
    assert session.voice == "longanhuan_v3"


def test_flashhead_runtime_face_refreshes_processor(tmp_path):
    face_path = tmp_path / "face.png"
    face_path.write_bytes(b"fake image")
    calls = []

    class FakeProcessor:
        def apply_reference_update(self, update_func):
            calls.append("locked")
            return update_func()

    handler = HandlerAvatarFlashHead()
    handler._default_condition_image_path = "/missing/default.png"
    handler.update_condition_image = lambda image_path: calls.append(image_path) or image_path
    context = SimpleNamespace(
        shared_states=SimpleNamespace(
            persona_runtime={"persona_id": "p_face", "face_image_path": str(face_path)}
        ),
        processor=FakeProcessor(),
        _applied_condition_image_path=None,
    )

    handler._apply_runtime_condition_image(context)

    assert calls == ["locked", str(face_path)]
    assert context._applied_condition_image_path == str(face_path)
