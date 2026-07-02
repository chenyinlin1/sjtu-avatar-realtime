from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel
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

