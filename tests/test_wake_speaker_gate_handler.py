from pathlib import Path


HANDLER_PATH = Path("src/handlers/voice_gate/wake_speaker_gate_handler.py")


def test_wake_speaker_gate_handler_declares_expected_stream_boundary():
    source = HANDLER_PATH.read_text(encoding="utf-8")

    for input_type in (
        "ChatDataType.MIC_AUDIO",
        "ChatDataType.HUMAN_DUPLEX_AUDIO",
        "ChatDataType.HUMAN_DUPLEX_TEXT",
    ):
        assert input_type in source

    for output_type in (
        "ChatDataType.HUMAN_AUDIO",
        "ChatDataType.HUMAN_TEXT",
        "ChatDataType.AVATAR_TEXT",
    ):
        assert output_type in source


def test_wake_speaker_gate_handler_keeps_asr_and_llm_external():
    source = HANDLER_PATH.read_text(encoding="utf-8")

    assert "module: asr" not in source
    assert "llm_handler" not in source
    assert "_submit_human_audio" in source
    assert "_submit_human_text" in source
    assert "_emit_interrupt" in source
