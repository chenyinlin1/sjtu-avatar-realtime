import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "handlers"))

from llm.openai_compatible.llm_handler_openai_compatible import HandlerLLM, LLMContext
from llm.openai_compatible.search_engine import SearchResult, format_search_results


def test_bocha_search_results_are_formatted_for_prompt():
    formatted = format_search_results([
        SearchResult(
            title="Example title",
            url="https://example.com/article",
            snippet="Example summary",
        )
    ])

    assert "Example title" in formatted
    assert "Example summary" in formatted
    assert "https://example.com/article" in formatted


def test_bocha_search_uses_natural_language_trigger_words():
    context = LLMContext("test-session")
    context.web_search_always = False
    handler = HandlerLLM()

    assert handler._should_search(context, "帮我搜一下今天的科技新闻")
    assert not handler._should_search(context, "你好，介绍一下你自己")


def test_bocha_search_can_be_forced_for_every_request():
    context = LLMContext("test-session")
    context.web_search_always = True
    handler = HandlerLLM()

    assert handler._should_search(context, "你好，介绍一下你自己")
