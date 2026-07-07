from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from chat_engine.data_models.engine_channel_type import EngineChannelType
from handlers.client.rtc_client.client_handler_rtc import (
    ClientHandlerRtc,
    ClientRtcConfigModel,
    ClientRtcContext,
    RtcClientSessionDelegate,
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


def _rtc_context_with_delegate():
    context = ClientRtcContext("test-session")
    context.client_session_delegate = RtcClientSessionDelegate()
    return context


def _avatar_audio_from(producer_name: str):
    return ChatData(
        type=ChatDataType.AVATAR_AUDIO,
        stream_id=ChatStreamIdentity(
            data_type=ChatDataType.AVATAR_AUDIO,
            builder_id=1,
            stream_id=1,
            producer_name=producer_name,
        ),
    )


def test_avatar_audio_passthrough_without_flashhead():
    handler = ClientHandlerRtc()
    context = _rtc_context_with_delegate()
    chat_data = _avatar_audio_from("CosyVoice")

    handler.handle(context, chat_data, {})

    queue = context.client_session_delegate.output_queues[EngineChannelType.AUDIO]
    assert queue.qsize() == 1
    assert queue.get_nowait() is chat_data


def test_flashhead_mode_skips_upstream_tts_avatar_audio():
    handler = ClientHandlerRtc()
    handler._find_flashhead_handler_name = lambda: "FlashHead"
    context = _rtc_context_with_delegate()

    handler.handle(context, _avatar_audio_from("CosyVoice"), {})

    queue = context.client_session_delegate.output_queues[EngineChannelType.AUDIO]
    assert queue.empty()


def test_flashhead_mode_forwards_flashhead_avatar_audio():
    handler = ClientHandlerRtc()
    handler._find_flashhead_handler_name = lambda: "FlashHead"
    context = _rtc_context_with_delegate()
    chat_data = _avatar_audio_from("FlashHead")

    handler.handle(context, chat_data, {})

    queue = context.client_session_delegate.output_queues[EngineChannelType.AUDIO]
    assert queue.qsize() == 1
    assert queue.get_nowait() is chat_data

