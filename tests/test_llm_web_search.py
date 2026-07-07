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


def test_bocha_search_skips_local_time_questions_even_when_forced(monkeypatch):
    context = LLMContext("test-session")
    context.web_search_always = True
    context.bocha_api_key = "fake-key"
    handler = HandlerLLM()

    def fail_search(*_args, **_kwargs):
        raise AssertionError("search_bocha should not be called for local time questions")

    monkeypatch.setattr(
        "llm.openai_compatible.llm_handler_openai_compatible.search_bocha",
        fail_search,
    )

    assert handler._build_bocha_search_context(context, "现在几点了") == ""


def _fake_web_search_schema():
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


def _fake_music_control_schema():
    return {
        "type": "function",
        "function": {
            "name": "music_control",
            "description": "control music",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
            },
        },
    }


def test_completion_kwargs_force_web_search_for_weather_query():
    context = LLMContext("test-session")
    context.enable_tool_definitions = True
    context.tool_schemas = [_fake_web_search_schema()]
    context.tool_choice = "auto"

    kwargs = HandlerLLM._build_completion_kwargs(
        context,
        [{"role": "user", "content": "今天上海的天气怎么样？"}],
        "今天上海的天气怎么样？",
    )

    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "web_search"}}


def test_completion_kwargs_force_web_search_for_recent_sports_results():
    context = LLMContext("test-session")
    context.enable_tool_definitions = True
    context.tool_schemas = [_fake_web_search_schema()]
    context.tool_choice = "auto"

    kwargs = HandlerLLM._build_completion_kwargs(
        context,
        [{"role": "user", "content": "昨天世界杯的赛果怎么样？"}],
        "昨天世界杯的赛果怎么样？",
    )

    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "web_search"}}


def test_completion_kwargs_do_not_force_web_search_for_plain_today_chat():
    context = LLMContext("test-session")
    context.enable_tool_definitions = True
    context.tool_schemas = [_fake_web_search_schema()]
    context.tool_choice = "auto"

    kwargs = HandlerLLM._build_completion_kwargs(
        context,
        [{"role": "user", "content": "今天晚上吃啥好？"}],
        "今天晚上吃啥好？",
    )

    assert kwargs["tool_choice"] == "auto"


def test_completion_kwargs_do_not_force_web_search_for_local_time_even_when_always_on():
    context = LLMContext("test-session")
    context.enable_tool_definitions = True
    context.tool_schemas = [_fake_web_search_schema()]
    context.tool_choice = "auto"
    context.web_search_always = True

    kwargs = HandlerLLM._build_completion_kwargs(
        context,
        [{"role": "user", "content": "现在几点了？"}],
        "现在几点了？",
    )

    assert kwargs["tool_choice"] == "auto"


def test_music_control_stop_recognizes_natural_stop_phrases():
    handler = HandlerLLM()

    for text in ["停止播放", "停止音乐", "停止放歌", "结束音乐", "关掉播放", "别播了", "不要放了"]:
        assert handler._extract_music_control(text) == {"action": "stop"}

    for text in ["暂停", "暂停。", "暂停！", "停。"]:
        assert handler._extract_music_control(text) == {"action": "pause"}


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


def test_completion_kwargs_prioritizes_music_control_over_web_search_when_music_playing():
    context = LLMContext("test-session")
    context.enable_tool_definitions = True
    context.tool_schemas = [_fake_web_search_schema(), _fake_music_control_schema()]
    context.tool_choice = "auto"
    context.web_search_always = True
    context.shared_states = SimpleNamespace(
        music_status={"state": "playing"},
        music_player_active=True,
    )

    kwargs = HandlerLLM._build_completion_kwargs(
        context,
        [{"role": "user", "content": "暂停。"}],
        "暂停。",
    )

    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "music_control"}}


def test_explicit_music_stop_can_be_direct_even_without_synced_music_status():
    context = LLMContext("test-session")
    context.shared_states = SimpleNamespace(music_status=None, music_player_active=False)

    control = HandlerLLM._extract_music_control("停止播放音乐。")

    assert control == {"action": "stop"}
    assert HandlerLLM._should_handle_music_control_direct(context, "停止播放音乐。", control)
