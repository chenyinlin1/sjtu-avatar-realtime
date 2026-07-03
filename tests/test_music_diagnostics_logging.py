from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LLM_HANDLER = ROOT / "src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py"
RTC_HANDLER = ROOT / "src/handlers/client/rtc_client/client_handler_rtc.py"


def test_backend_music_tool_diagnostics_are_logged():
    source = LLM_HANDLER.read_text()

    assert "Music tool result" in source
    assert "Music client_action dispatch" in source
    assert "Music request completed without playable URL" in source
    assert "_summarize_url_for_log" in source


def test_rtc_client_action_send_is_logged():
    source = RTC_HANDLER.read_text()

    assert "RTC chat channel client_action send" in source
