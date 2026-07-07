from types import SimpleNamespace

from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import ChatSignalSourceType, ChatSignalType
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from chat_engine.data_models.engine_channel_type import EngineChannelType
from handlers.client.rtc_client.client_handler_rtc import (
    ClientHandlerRtc,
    ClientRtcConfigModel,
)


def test_video_definition_uses_configured_output_video_fps():
    handler = ClientHandlerRtc()

    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel(output_video_fps=25))

    video_entry = handler.output_bundle_definitions[EngineChannelType.VIDEO].get_main_entry()
    assert handler.rtc_streamer_factory.fps == 25
    assert video_entry.sample_rate == 25


def test_frontend_init_config_can_disable_user_video_capture():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel(input_video_enabled=False))

    config = handler.build_frontend_init_config({}, None)

    assert config["track_constraints"]["audio"]["sampleRate"] == 16000
    assert config["track_constraints"]["audio"]["channelCount"] == 1
    assert config["track_constraints"]["video"] is False

def test_frontend_init_config_can_enable_user_video_capture():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel(input_video_enabled=True))

    config = handler.build_frontend_init_config({}, None)

    assert config["track_constraints"]["video"] == {}



class _FakeRtcStream:
    chat_channel = None

    def __init__(self):
        self.calls = []

    def request_av_sync_reset(self, reason="", target_speech_id=None, wait_for_audio=False):
        self.calls.append(("reset", reason, target_speech_id, wait_for_audio))

    def finish_av_sync_playback(self, reason=""):
        self.calls.append(("finish", reason))


class _FakeStreamManager:
    def __init__(self, streams=None):
        self.streams = streams or {}

    def find_stream(self, stream_id):
        key = stream_id.stream_key_str if stream_id is not None else None
        return self.streams.get(key, SimpleNamespace(source_streams={}, ancestor_streams=[]))


def _signal_context(session_id="test-session", streams=None):
    return SimpleNamespace(session_id=session_id, stream_manager=_FakeStreamManager(streams))


def test_flashhead_client_playback_begin_requests_targeted_rtc_av_sync_reset():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel())
    fake_stream = _FakeRtcStream()
    handler.rtc_streamer_factory.streams["test-session"] = fake_stream
    playback = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=12,
        stream_id=34,
        producer_name="FlashHead",
    )
    avatar_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=56,
        stream_id=78,
        producer_name="CosyVoice",
    )
    playback_stream = SimpleNamespace(
        source_streams={avatar_audio.key: avatar_audio},
        ancestor_streams=[avatar_audio],
    )
    signal = ChatSignal(
        type=ChatSignalType.STREAM_BEGIN,
        source_type=ChatSignalSourceType.HANDLER,
        related_stream=playback,
    )

    handler.on_signal(_signal_context(streams={playback.stream_key_str: playback_stream}), signal)

    assert fake_stream.calls == [("reset", "stream_12_34:stream_begin", "stream_56_78", True)]


def test_non_flashhead_client_playback_begin_does_not_request_targeted_reset():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel())
    fake_stream = _FakeRtcStream()
    handler.rtc_streamer_factory.streams["test-session"] = fake_stream
    playback = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=12,
        stream_id=34,
        producer_name="LiteAvatar",
    )
    signal = ChatSignal(
        type=ChatSignalType.STREAM_BEGIN,
        source_type=ChatSignalSourceType.HANDLER,
        related_stream=playback,
    )

    handler.on_signal(_signal_context(), signal)

    assert fake_stream.calls == []


def test_client_playback_cancel_requests_rtc_av_sync_reset():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel())
    fake_stream = _FakeRtcStream()
    handler.rtc_streamer_factory.streams["test-session"] = fake_stream
    playback = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=12,
        stream_id=34,
        producer_name="FlashHead",
    )
    signal = ChatSignal(
        type=ChatSignalType.STREAM_CANCEL,
        source_type=ChatSignalSourceType.HANDLER,
        related_stream=playback,
    )

    handler.on_signal(_signal_context(), signal)

    assert fake_stream.calls == [("reset", "stream_12_34:stream_cancel", None, False)]


def test_client_playback_end_finishes_rtc_av_sync_tracking():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel())
    fake_stream = _FakeRtcStream()
    handler.rtc_streamer_factory.streams["test-session"] = fake_stream
    playback = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=12,
        stream_id=34,
        producer_name="FlashHead",
    )
    signal = ChatSignal(
        type=ChatSignalType.STREAM_END,
        source_type=ChatSignalSourceType.HANDLER,
        related_stream=playback,
    )

    handler.on_signal(_signal_context(), signal)

    assert fake_stream.calls == [("finish", "stream_12_34:stream_end")]


def test_non_client_playback_cancel_does_not_request_rtc_av_sync_reset():
    handler = ClientHandlerRtc()
    handler.load(ChatEngineConfigModel(), ClientRtcConfigModel())
    fake_stream = _FakeRtcStream()
    handler.rtc_streamer_factory.streams["test-session"] = fake_stream
    avatar_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=12,
        stream_id=34,
        producer_name="CosyVoice",
    )
    signal = ChatSignal(
        type=ChatSignalType.STREAM_CANCEL,
        source_type=ChatSignalSourceType.HANDLER,
        related_stream=avatar_audio,
    )

    handler.on_signal(_signal_context(), signal)

    assert fake_stream.calls == []
