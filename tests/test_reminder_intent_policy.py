from types import SimpleNamespace

import pytest

from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundleDefinition,
    DataBundleEntry,
)
from handlers.agent.tools.reminder.intent_policy import (
    MANAGE_REMINDER_TOOL,
    forced_reminder_tool_choice,
    missing_forced_reminder_reply,
    should_force_create_reminder,
)
from handlers.agent.tools.reminder.prompt_rules import (
    REMINDER_SYSTEM_RULES,
    REMINDER_TOOL_DESCRIPTION,
    REMINDER_TOOL_MISSING_REPLY,
)
from handlers.llm.openai_compatible.llm_handler_openai_compatible import HandlerLLM
from handlers.llm.openai_compatible import reminder_response_guard


@pytest.mark.parametrize(
    "text",
    [
        "今晚9点提醒我睡觉。",
        "明早7点提醒我起床。",
        "提醒我明天上午九点开会。",
        "半小时后提醒我喝水。",
        "每天早上八点叫我量血压。",
    ],
)
def test_clear_create_intent_is_forced(text):
    assert should_force_create_reminder(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "明天提醒我喝水。",
        "今晚提醒我睡觉。",
        "今晚9点提醒我。",
        "取消今晚九点的提醒。",
        "不要再提醒我睡觉。",
        "今晚九点关闭SOS预警。",
        "今晚九点我准备睡觉。",
    ],
)
def test_ambiguous_cancel_or_non_reminder_intent_is_not_forced(text):
    assert should_force_create_reminder(text) is False


def test_forced_choice_requires_enabled_registered_tool():
    forced = forced_reminder_tool_choice(
        "今晚9点提醒我睡觉",
        available_tools=[MANAGE_REMINDER_TOOL],
        configured_choice="auto",
    )

    assert forced == {
        "type": "function",
        "function": {"name": MANAGE_REMINDER_TOOL},
    }
    assert (
        forced_reminder_tool_choice(
            "今晚9点提醒我睡觉",
            available_tools=[],
            configured_choice="auto",
        )
        is None
    )
    assert (
        forced_reminder_tool_choice(
            "今晚9点提醒我睡觉",
            available_tools=[MANAGE_REMINDER_TOOL],
            configured_choice="none",
        )
        is None
    )


def test_handler_routes_clear_reminder_before_other_auto_tools():
    context = SimpleNamespace(
        tool_choice="auto",
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": MANAGE_REMINDER_TOOL, "parameters": {}},
            }
        ],
        web_search_always=False,
        shared_states=None,
    )

    assert HandlerLLM._tool_choice_for_turn(context, "今晚9点提醒我睡觉") == {
        "type": "function",
        "function": {"name": MANAGE_REMINDER_TOOL},
    }
    assert HandlerLLM._tool_choice_for_turn(context, "明天提醒我喝水") == "auto"


def test_missing_forced_call_uses_safe_reply_not_model_success_text():
    choice = {
        "type": "function",
        "function": {"name": MANAGE_REMINDER_TOOL},
    }

    assert missing_forced_reminder_reply(choice, has_tool_calls=False) == (
        REMINDER_TOOL_MISSING_REPLY
    )
    assert missing_forced_reminder_reply(choice, has_tool_calls=True) is None
    assert "记好了" not in REMINDER_TOOL_MISSING_REPLY


def test_forced_reminder_initial_text_can_be_buffered_without_streaming():
    delta = SimpleNamespace(content="记好了，到时候会提醒您。", tool_calls=None)
    completion = [SimpleNamespace(choices=[SimpleNamespace(delta=delta)])]
    context = SimpleNamespace(active_stream_keys=set(), output_texts="")
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))

    class RecordingStreamer:
        def __init__(self):
            self.calls = []

        def stream_data(self, bundle):
            self.calls.append(bundle)

    streamer = RecordingStreamer()
    full_text, tool_calls, cancelled = HandlerLLM._stream_completion_response(
        context,
        completion,
        definition,
        streamer,
        None,
        emit_text=False,
    )

    assert full_text == "记好了，到时候会提醒您。"
    assert tool_calls == []
    assert cancelled is False
    assert context.output_texts == ""
    assert streamer.calls == []


def test_prompt_and_tool_description_prioritize_mandatory_tool_call():
    assert "本轮必须只调用 manage_reminder" in REMINDER_SYSTEM_RULES
    assert "不得先输出任何自然语言" in REMINDER_SYSTEM_RULES
    assert "不得口头假装已经创建" in REMINDER_TOOL_DESCRIPTION
    assert "真实 action_ack" in REMINDER_TOOL_DESCRIPTION


def test_output_guard_emits_only_safe_reply_when_forced_call_is_missing(monkeypatch):
    audits = []
    monkeypatch.setattr(
        reminder_response_guard,
        "audit_event",
        lambda _context, event, **payload: audits.append((event, payload)),
    )
    context = SimpleNamespace(model_name="test-model", output_texts="")
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))

    class RecordingStreamer:
        def __init__(self):
            self.bundles = []

        def stream_data(self, bundle):
            self.bundles.append(bundle)

    streamer = RecordingStreamer()
    choice = {
        "type": "function",
        "function": {"name": MANAGE_REMINDER_TOOL},
    }

    emitted = reminder_response_guard.emit_missing_reminder_tool_reply(
        context=context,
        tool_choice=choice,
        has_tool_calls=False,
        cancelled=False,
        model_output="记好了，到时候会提醒您。",
        user_text="今晚9点提醒我睡觉",
        output_definition=definition,
        streamer=streamer,
        stream_identity="input-1",
        stream_key="stream-1",
        turn_id="turn-1",
    )

    assert emitted is True
    assert context.output_texts == REMINDER_TOOL_MISSING_REPLY
    assert streamer.bundles[0].get_main_data() == REMINDER_TOOL_MISSING_REPLY
    assert audits[0][1]["expected_tool"] == MANAGE_REMINDER_TOOL
