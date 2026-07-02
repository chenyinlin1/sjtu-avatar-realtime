import base64
from pathlib import Path

import yaml

from handlers.tts.bailian_tts.tts_handler_qwen_realtime import TTSConfig, decode_audio_delta


CONFIG_PATH = Path("config/chat_with_openai_compatible_bailian_qwen_realtime_flashhead_6006.yaml")


def load_handler_configs():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return config["default"]["chat_engine"]["handler_configs"]


def test_start_script_uses_qwen_realtime_config():
    start_script = Path("start_realtime_human.sh").read_text(encoding="utf-8")

    assert str(CONFIG_PATH) in start_script


def test_qwen_realtime_config_uses_realtime_tts_handler():
    handler_configs = load_handler_configs()

    assert handler_configs["CosyVoice"]["module"] == "tts/bailian_tts/tts_handler_qwen_realtime"
    assert handler_configs["CosyVoice"]["model_name"] == "qwen3-tts-flash-realtime"
    assert handler_configs["CosyVoice"]["voice"] == "Eric"


def test_qwen_realtime_tts_config_defaults_to_eric_voice():
    assert TTSConfig().voice == "Eric"


def test_decode_audio_delta_accepts_base64_pcm_payload():
    pcm = b"\x01\x02\x03\x04"
    message = {"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode("ascii")}

    assert decode_audio_delta(message) == pcm
