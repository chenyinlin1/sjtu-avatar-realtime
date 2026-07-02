from pathlib import Path

import yaml


CONFIG_PATH = Path("config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml")


def load_handler_configs():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return config["default"]["chat_engine"]["handler_configs"]


def test_6006_config_enables_camera_upstream_without_llm_vision():
    handler_configs = load_handler_configs()

    assert handler_configs["RtcClient"]["input_video_enabled"] is True
    assert handler_configs["LLMOpenAICompatible"]["enable_video_input"] is False
