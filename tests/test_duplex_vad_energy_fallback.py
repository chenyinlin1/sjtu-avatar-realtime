from pathlib import Path


DUPLEX_VAD_PATH = Path("src/handlers/vad/silerovad/duplex_vad_handler.py")


def test_duplex_vad_logs_raw_model_probability_for_diagnostics():
    source = DUPLEX_VAD_PATH.read_text(encoding="utf-8")

    assert "raw_speech_prob" in source
    assert "Duplex VAD diag" in source

def test_duplex_vad_energy_fallback_only_starts_speech_streams():
    source = DUPLEX_VAD_PATH.read_text(encoding="utf-8")
    guard = source.split("speech_prob, energy_fallback_used = _apply_energy_speech_fallback", 1)[0].rsplit("if context.speaking_status in", 1)[1]

    assert "SpeakingStatus.END" in guard
    assert "SpeakingStatus.PRE_START" in guard
    assert "SpeakingStatus.START" not in guard
    assert "SpeakingStatus.POST_END" not in guard
