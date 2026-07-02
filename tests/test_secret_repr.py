from handlers.llm.openai_compatible.llm_handler_openai_compatible import LLMConfig
from handlers.tts.bailian_tts.tts_handler_cosyvoice_bailian import TTSConfig


def test_runtime_config_repr_does_not_expose_api_keys():
    secret = "sk-test-secret"

    assert secret not in repr(LLMConfig(api_key=secret))
    assert secret not in repr(TTSConfig(api_key=secret))
