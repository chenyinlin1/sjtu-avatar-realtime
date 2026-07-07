from types import SimpleNamespace

from handlers.tts.bailian_tts import tts_handler_cosyvoice_bailian as tts_module
from handlers.tts.bailian_tts.tts_handler_cosyvoice_bailian import CosyvoiceCallBack, HandlerTTS, TTSContext


class ActionOnlyBundle:
    metadata = {
        "client_action": {
            "type": "music.play",
            "url": "https://example.com/song.mp3",
        }
    }

    def get_main_data(self):
        return ""


class TextBundle:
    metadata = {}

    def __init__(self, text):
        self.text = text

    def get_main_data(self):
        return self.text


class FakeSynthesizer:
    def streaming_call(self, text):
        raise RuntimeError("tts closed")

    def streaming_cancel(self):
        pass


def test_tts_skips_action_only_empty_text_without_creating_session():
    context = TTSContext("test-session")
    handler = HandlerTTS()
    data = SimpleNamespace(
        stream_id=SimpleNamespace(key="avatar-text-stream"),
        data=ActionOnlyBundle(),
        is_last_data=False,
    )

    context.handle_text_stream(data, handler)

    assert context.api_links == {}


def test_tts_does_not_restart_failed_input_stream(monkeypatch):
    created_streams = []

    class FakeStreamer:
        data_definition = object()

        def new_stream(self, *args, **kwargs):
            key = f"avatar-audio-{len(created_streams)}"
            created_streams.append(key)
            return SimpleNamespace(key=key)

    context = TTSContext("test-session")
    context.data_submitter = SimpleNamespace(
        get_streamer=lambda data_type: FakeStreamer(),
    )
    handler = HandlerTTS()
    input_stream = SimpleNamespace(key="avatar-text-stream")

    monkeypatch.setattr(tts_module, "audit_event", lambda *args, **kwargs: "turn-id")
    monkeypatch.setattr(
        context,
        "_ensure_synthesizer",
        lambda session, handler: setattr(session, "synthesizer", FakeSynthesizer()),
    )

    context.handle_text_stream(
        SimpleNamespace(
            stream_id=input_stream,
            data=TextBundle("第一段"),
            is_last_data=False,
        ),
        handler,
    )
    context.handle_text_stream(
        SimpleNamespace(
            stream_id=input_stream,
            data=TextBundle("第二段"),
            is_last_data=False,
        ),
        handler,
    )
    context.handle_text_stream(
        SimpleNamespace(
            stream_id=input_stream,
            data=TextBundle(""),
            is_last_data=True,
        ),
        handler,
    )

    assert created_streams == ["avatar-audio-0"]
    assert context.api_links == {}
    assert context.failed_input_stream_keys == set()


def test_tts_passes_configured_synthesizer_parameters(monkeypatch):
    captured = {}

    class FakeStreamer:
        data_definition = object()

    class CapturingSynthesizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    context = TTSContext("test-session")
    context.data_submitter = SimpleNamespace(
        get_streamer=lambda data_type: FakeStreamer(),
    )
    handler = HandlerTTS()
    handler.model_name = "cosyvoice-v3.5-flash"
    handler.voice = "cosyvoice-v3.5-flash-test-voice"
    handler.instruction = "请用四川话表达。"
    handler.volume = 60
    handler.speech_rate = 1.1
    handler.pitch_rate = 0.95
    handler.seed = 7
    handler.synthesis_type = 0
    handler.language_hints = ["zh"]
    handler.additional_params = {"enable_markdown_filter": True}
    session = context._create_session(SimpleNamespace(key="avatar-text-stream"))

    monkeypatch.setattr(tts_module, "SpeechSynthesizer", CapturingSynthesizer)

    context._ensure_synthesizer(session, handler)

    assert captured["model"] == "cosyvoice-v3.5-flash"
    assert captured["voice"] == "cosyvoice-v3.5-flash-test-voice"
    assert captured["instruction"] == "请用四川话表达。"
    assert captured["volume"] == 60
    assert captured["speech_rate"] == 1.1
    assert captured["pitch_rate"] == 0.95
    assert captured["seed"] == 7
    assert captured["synthesis_type"] == 0
    assert captured["language_hints"] == ["zh"]
    assert captured["additional_params"] == {"enable_markdown_filter": True}


def test_tts_callback_error_marks_failed_input_stream(monkeypatch):
    context = TTSContext("test-session")
    input_stream = SimpleNamespace(key="avatar-text-stream")
    session = context._create_session(input_stream)
    session.output_stream_key = "avatar-audio-0"
    session.turn_id = "turn-id"
    session.model_name = "cosyvoice-v3.5-flash"
    session.voice = "cosyvoice-v3.5-flash-test-voice"
    context.api_links[input_stream.key] = session
    callback = CosyvoiceCallBack(context, object(), session)

    monkeypatch.setattr(tts_module, "audit_event", lambda *args, **kwargs: "turn-id")
    monkeypatch.setattr(callback, "_submit_end_frame", lambda: None)

    callback.on_error("service failed")

    assert context.api_links == {}
    assert context.failed_input_stream_keys == {"avatar-text-stream"}
    assert session.cancelled is True
