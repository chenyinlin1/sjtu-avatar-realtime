import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "handlers"))

from llm.openai_compatible.llm_handler_openai_compatible import LLMConfig
from handlers.tts.bailian_tts.tts_handler_cosyvoice_bailian import TTSConfig


def test_runtime_config_repr_does_not_expose_api_keys():
    secret = "sk-test-secret"
    bocha_secret = "bocha-test-secret"

    assert secret not in repr(LLMConfig(api_key=secret))
    assert bocha_secret not in repr(LLMConfig(bocha_api_key=bocha_secret))
    assert secret not in repr(TTSConfig(api_key=secret))
