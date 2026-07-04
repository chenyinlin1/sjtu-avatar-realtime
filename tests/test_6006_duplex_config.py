from pathlib import Path

import yaml


CONFIG_PATH = Path("config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml")
SMART_TURN_MODEL = Path("models/smart_turn/smart-turn-v3.1-cpu.onnx")


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["default"]


def test_6006_config_uses_duplex_interrupt_pipeline():
    config = load_config()
    handlers = config["chat_engine"]["handler_configs"]

    assert "history" in config
    assert config["history"]["retention_mode"] == "by_both"

    assert handlers["SileroVad"]["module"] == "vad/silerovad/duplex_vad_handler"
    assert handlers["SileroVad"]["reconnect_threshold_samples"] == 0
    assert handlers["SileroVad"]["post_end_monitor_samples"] == 8000
    assert handlers["SileroVad"].get("energy_speech_threshold") is None
    assert handlers["SenseVoice"]["input_type_override"] == {
        "HUMAN_AUDIO": "HUMAN_DUPLEX_AUDIO",
    }
    assert handlers["SenseVoice"]["output_type_override"] == {
        "HUMAN_TEXT": "HUMAN_DUPLEX_TEXT",
    }

    assert handlers["SmartTurnEOU"]["enabled"] is True
    assert handlers["SmartTurnEOU"]["module"] == "vad/smart_turn_eou/eou_handler_smart_turn"
    assert handlers["SmartTurnEOU"]["model_path"] == str(SMART_TURN_MODEL)
    assert SMART_TURN_MODEL.exists()

    semantic = handlers["SemanticTurnDetector"]
    assert semantic["enabled"] is True
    assert semantic["duplex_mode"] is True
    assert semantic["enable_interrupt_detection"] is True


def test_6006_config_keeps_transport_camera_and_llm_vision_choices():
    handlers = load_config()["chat_engine"]["handler_configs"]

    assert handlers["RtcClient"]["input_video_enabled"] is True
    assert handlers["RtcClient"]["output_video_fps"] == 25
    assert handlers["FlashHead"]["fps"] == 25
    assert handlers["LLMOpenAICompatible"]["model_name"] == "deepseek-v4-flash"
    assert handlers["LLMOpenAICompatible"]["enable_video_input"] is False
