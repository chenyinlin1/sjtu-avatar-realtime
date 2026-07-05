import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "handlers"))

from chat_engine.data_models.chat_signal_type import ChatSignalSourceType, ChatSignalType
from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundleDefinition,
    DataBundleEntry,
)
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


def test_local_time_context_is_injected_for_current_time_questions():
    context = LLMContext("test-session")
    handler = HandlerLLM()

    time_context = handler._build_local_time_context(context, "现在几点了")

    assert time_context is not None
    assert time_context["role"] == "user"
    assert "当前日期时间" in time_context["content"]
    assert "Asia/Shanghai" in time_context["content"]
    assert handler._build_local_time_context(context, "你好，介绍一下你自己") is None


def test_music_control_stop_recognizes_natural_stop_phrases():
    handler = HandlerLLM()

    for text in ["停止播放", "停止音乐", "停止放歌", "结束音乐", "关掉播放", "别播了", "不要放了"]:
        assert handler._extract_music_control(text) == {"action": "stop"}

    assert handler._extract_music_control("暂停") == {"action": "pause"}


class FakeHistory:
    def __init__(self):
        self.messages = []

    def add_message(self, message):
        self.messages.append(message)


class FakeStreamer:
    def __init__(self):
        self.outputs = []

    def stream_data(self, output, **kwargs):
        self.outputs.append((output, kwargs))


def test_music_stop_control_interrupts_pending_avatar_response_streams():
    emitted_signals = []
    context = SimpleNamespace(
        active_stream_keys=set(),
        music_player_active=True,
        history=FakeHistory(),
        input_texts="退出音乐",
        output_texts="",
        owner="LLMOpenAICompatible",
        emit_signal=emitted_signals.append,
    )
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))
    streamer = FakeStreamer()

    HandlerLLM()._handle_music_control(
        context,
        {"action": "stop"},
        definition,
        streamer,
        "stream-test",
        "退出音乐",
    )

    assert context.music_player_active is False
    assert emitted_signals
    assert emitted_signals[0].type == ChatSignalType.INTERRUPT
    assert emitted_signals[0].source_type == ChatSignalSourceType.HANDLER
    assert emitted_signals[0].signal_data["reason"] == "music_stop"
