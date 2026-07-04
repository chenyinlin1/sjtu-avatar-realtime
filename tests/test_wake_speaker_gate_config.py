from pathlib import Path

import yaml


CONFIG_PATH = Path("config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml")


def test_6006_config_routes_sensevoice_through_wake_speaker_gate():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    handlers = config["default"]["chat_engine"]["handler_configs"]

    assert handlers["WakeSpeakerGate"]["module"] == "voice_gate/wake_speaker_gate_handler"
    assert handlers["WakeSpeakerGate"]["kws_enabled"] is True
    assert handlers["WakeSpeakerGate"]["kws_auto_download"] is True
    assert handlers["WakeSpeakerGate"]["kws_model_url"].startswith(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    )
    assert handlers["WakeSpeakerGate"]["speaker_gate_enabled"] is True
    assert handlers["SenseVoice"].get("input_type_override") is None
    assert handlers["SenseVoice"]["output_type_override"] == {
        "HUMAN_TEXT": "HUMAN_DUPLEX_TEXT"
    }
    assert handlers["SemanticTurnDetector"]["enabled"] is False
