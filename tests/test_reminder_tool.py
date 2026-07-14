import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundleDefinition,
    DataBundleEntry,
)
from handlers.agent.prompt.elder_profile_prompt import build_elder_profile_prompt
from handlers.agent.tools.reminder.action_builder import (
    build_cancel_action,
    build_create_action,
)
from handlers.agent.tools.reminder.time_utils import (
    AmbiguousReminderTimeError,
    PastReminderTimeError,
    normalize_remind_at,
)
from handlers.agent.tools.reminder.tool import ManageReminderTool
from handlers.llm.openai_compatible.llm_handler_openai_compatible import (
    HandlerLLM,
    XIAOBAN_SYSTEM_PROMPT,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def make_context(reminders=None, *, timezone_name="Asia/Shanghai"):
    profile = {}
    if reminders is not None:
        profile["reminders"] = reminders
    shared_states = SimpleNamespace(
        device_info={
            "device_sn": "speaker-1",
            "timezone": timezone_name,
            "elder_profile": profile,
        }
    )
    return SimpleNamespace(
        session_id="reminder-session",
        shared_states=shared_states,
        local_time_timezone="UTC",
    )


def future_iso(hours=2):
    return (datetime.now(SHANGHAI) + timedelta(hours=hours)).isoformat()


def test_manage_reminder_schema_is_registered_as_one_formal_function():
    schema = ManageReminderTool(context=make_context([])).get_openai_schema()
    function = schema["function"]

    assert function["name"] == "manage_reminder"
    assert function["parameters"]["properties"]["operation"]["enum"] == [
        "create",
        "cancel",
    ]
    assert function["parameters"]["properties"]["repeat"]["enum"] == [
        "none",
        "daily",
        "weekly",
    ]
    assert function["parameters"]["required"] == ["operation"]
    assert "明确确认" in function["description"]
    assert "危急" in function["description"]


@pytest.mark.parametrize(
    "args,error",
    [
        ({"operation": "create", "remind_at": "明天早上八点"}, "title is required"),
        ({"operation": "create", "title": "喝水"}, "remind_at is required"),
        (
            {
                "operation": "create",
                "title": "喝水",
                "remind_at": "明天早上八点",
                "repeat": "monthly",
            },
            "repeat must be",
        ),
        ({"operation": "cancel", "confirmed": True}, "entity_id is required"),
        (
            {"operation": "cancel", "entity_id": 123, "confirmed": False},
            "explicit user confirmation",
        ),
    ],
)
def test_manage_reminder_rejects_incomplete_or_unsafe_arguments(args, error):
    result = ManageReminderTool(context=make_context([])).execute(args)

    assert result.success is False
    assert error in result.error


def test_create_builds_v1_action_id_and_args_with_epoch_milliseconds():
    result = ManageReminderTool(context=make_context([])).execute(
        {
            "operation": "create",
            "title": "吃降压药",
            "remind_at": future_iso(),
            "repeat": "none",
            "speak_text": "该吃降压药啦",
        }
    )

    assert result.success is True
    action = result.data["client_action"]
    assert action["type"] == "reminder.create"
    assert action["action_id"].startswith("reminder-")
    assert action["args"]["title"] == "吃降压药"
    assert isinstance(action["args"]["remind_at"], int)
    assert action["args"]["remind_at"] > 100_000_000_000
    assert action["args"]["repeat"] == "none"
    assert action["args"]["speak_text"] == "该吃降压药啦"
    assert "title" not in {key for key in action if key != "args"}


def test_cancel_requires_snapshot_match_and_builds_confirmed_v1_action():
    context = make_context(
        [{"id": 123, "title": "吃药", "remind_at": 1_800_000_000_000}]
    )
    result = ManageReminderTool(context=context).execute(
        {
            "operation": "cancel",
            "entity_id": 123,
            "confirmed": True,
        }
    )

    assert result.success is True
    assert result.data["reminder"]["title"] == "吃药"
    assert result.data["client_action"] == {
        "type": "reminder.cancel",
        "action_id": result.data["action_id"],
        "args": {"entity_id": 123},
        "confirm": True,
    }

    missing_snapshot = ManageReminderTool(context=make_context()).execute(
        {
            "operation": "cancel",
            "entity_id": 123,
            "confirmed": True,
        }
    )
    assert missing_snapshot.success is False
    assert "do not guess" in missing_snapshot.error


def test_formal_medication_plan_mutation_is_rejected():
    result = ManageReminderTool(context=make_context([])).execute(
        {
            "operation": "create",
            "title": "把降压药剂量改成两片",
            "remind_at": future_iso(),
        }
    )
    assert result.success is False
    assert "medication plans" in result.error


def test_action_builders_never_emit_legacy_flat_fields():
    create = build_create_action(
        title="喝水",
        remind_at_ms=1_800_000_000_000,
        repeat="none",
        action_id="create-1",
    )
    cancel = build_cancel_action(entity_id=42, action_id="cancel-1")

    assert create == {
        "type": "reminder.create",
        "action_id": "create-1",
        "args": {"title": "喝水", "remind_at": 1_800_000_000_000, "repeat": "none"},
    }
    assert cancel == {
        "type": "reminder.cancel",
        "action_id": "cancel-1",
        "args": {"entity_id": 42},
        "confirm": True,
    }


def test_time_normalization_covers_calendar_relative_repeat_and_cross_day():
    now = datetime(2026, 7, 13, 10, 15, tzinfo=SHANGHAI)

    tomorrow = normalize_remind_at(
        "明天早上八点", timezone_name="Asia/Shanghai", now=now
    )
    assert datetime.fromtimestamp(tomorrow / 1000, SHANGHAI) == datetime(
        2026, 7, 14, 8, 0, tzinfo=SHANGHAI
    )

    later = normalize_remind_at("半小时后", timezone_name="Asia/Shanghai", now=now)
    assert later == int((now + timedelta(minutes=30)).timestamp() * 1000)

    daily = normalize_remind_at(
        "每天早上八点", timezone_name="Asia/Shanghai", now=now, repeat="daily"
    )
    assert datetime.fromtimestamp(daily / 1000, SHANGHAI) == datetime(
        2026, 7, 14, 8, 0, tzinfo=SHANGHAI
    )

    next_monday = normalize_remind_at(
        "下周一上午九点", timezone_name="Asia/Shanghai", now=now
    )
    assert datetime.fromtimestamp(next_monday / 1000, SHANGHAI) == datetime(
        2026, 7, 20, 9, 0, tzinfo=SHANGHAI
    )

    near_midnight = datetime(2026, 7, 13, 23, 50, tzinfo=SHANGHAI)
    cross_day = normalize_remind_at(
        "半小时后", timezone_name="Asia/Shanghai", now=near_midnight
    )
    assert datetime.fromtimestamp(cross_day / 1000, SHANGHAI) == datetime(
        2026, 7, 14, 0, 20, tzinfo=SHANGHAI
    )


@pytest.mark.parametrize(
    "value",
    ["2026-07-13 22:00:00", "2026-07-13T22:00:00"],
)
def test_time_normalization_accepts_common_llm_datetime_formats(value):
    now = datetime(2026, 7, 13, 20, 0, tzinfo=SHANGHAI)

    remind_at = normalize_remind_at(value, timezone_name="Asia/Shanghai", now=now)

    assert datetime.fromtimestamp(remind_at / 1000, SHANGHAI) == datetime(
        2026, 7, 13, 22, 0, tzinfo=SHANGHAI
    )


def test_time_normalization_uses_device_timezone_and_rejects_past_or_ambiguous_time():
    same_instant = datetime(2026, 7, 13, 22, 0, tzinfo=timezone.utc)
    shanghai = normalize_remind_at(
        "明天早上八点", timezone_name="Asia/Shanghai", now=same_instant
    )
    new_york = normalize_remind_at(
        "明天早上八点", timezone_name="America/New_York", now=same_instant
    )
    assert shanghai != new_york

    with pytest.raises(PastReminderTimeError):
        normalize_remind_at(
            "今天早上八点",
            timezone_name="Asia/Shanghai",
            now=datetime(2026, 7, 13, 10, 0, tzinfo=SHANGHAI),
        )
    with pytest.raises(AmbiguousReminderTimeError):
        normalize_remind_at(
            "明天提醒我吃药", timezone_name="Asia/Shanghai", now=same_instant
        )


def test_full_create_then_cancel_chain_mounts_metadata_and_waits_for_real_ack():
    context = make_context([])
    tool = ManageReminderTool(context=context)
    create_result = tool.execute(
        {
            "operation": "create",
            "title": "喝水",
            "remind_at": future_iso(),
            "repeat": "none",
        }
    )
    normalized = {
        "name": "manage_reminder",
        "success": create_result.success,
        "data": create_result.data,
        "error": create_result.error,
        "content": create_result.to_content_str(),
    }

    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))
    sent_actions = []
    registry = context.shared_states.reminder_pending_actions

    class AckingStreamer:
        def __init__(self, entity_id):
            self.entity_id = entity_id

        def stream_data(self, bundle):
            action = bundle.metadata["client_action"]
            sent_actions.append(action)
            registry.resolve(
                {
                    "action_id": action["action_id"],
                    "ok": True,
                    "entity_id": self.entity_id,
                    "error": None,
                }
            )

    dispatched = HandlerLLM._dispatch_reminder_tool_results(
        SimpleNamespace(
            shared_states=context.shared_states,
            session_id=context.session_id,
        ),
        [normalized],
        definition,
        AckingStreamer(321),
        "stream-1",
    )
    assert dispatched is True
    assert sent_actions[0] == create_result.data["client_action"]
    assert normalized["data"]["ok"] is True
    assert normalized["data"]["entity_id"] == 321
    assert json.loads(normalized["content"])["status"] == "succeeded"
    assert (
        context.shared_states.device_info["elder_profile"]["reminders"][0]["id"] == 321
    )

    cancel_result = tool.execute(
        {"operation": "cancel", "entity_id": 321, "confirmed": True}
    )
    cancel_normalized = {
        "name": "manage_reminder",
        "success": cancel_result.success,
        "data": cancel_result.data,
        "error": cancel_result.error,
        "content": cancel_result.to_content_str(),
    }
    HandlerLLM._dispatch_reminder_tool_results(
        SimpleNamespace(
            shared_states=context.shared_states, session_id=context.session_id
        ),
        [cancel_normalized],
        definition,
        AckingStreamer(None),
        "stream-2",
    )
    assert sent_actions[-1]["type"] == "reminder.cancel"
    assert sent_actions[-1]["confirm"] is True
    assert cancel_normalized["data"]["ok"] is True
    assert context.shared_states.device_info["elder_profile"]["reminders"] == []


def test_reminder_snapshot_exposes_ids_local_times_and_confirmation_boundary():
    prompt = build_elder_profile_prompt(
        {
            "timezone": "Asia/Shanghai",
            "elder_profile": {
                "reminders": [
                    {"id": 123, "title": "吃药", "remind_at": 1_783_987_200_000},
                ]
            },
        }
    )

    assert "当前提醒快照" in prompt
    assert "entity_id=123" in prompt
    assert "吃药" in prompt
    assert "明确确认" in prompt
    assert "不得猜测" in prompt
    assert "ALERT" in prompt


def test_missing_reminders_remains_backward_compatible():
    assert build_elder_profile_prompt({"elder_profile": {}}) == ""


def test_empty_reminder_snapshot_is_explicitly_safe():
    prompt = build_elder_profile_prompt({"elder_profile": {"reminders": []}})
    assert "当前快照为空" in prompt
    assert "不得猜测" in prompt


def test_system_prompt_requires_cancel_confirmation_and_real_ack():
    assert "当前提醒快照确定唯一 entity_id" in XIAOBAN_SYSTEM_PROMPT
    assert "首次提出取消时只复述具体提醒并询问是否确认" in XIAOBAN_SYSTEM_PROMPT
    assert "后续一轮明确确认" in XIAOBAN_SYSTEM_PROMPT
    assert "confirmed=true" in XIAOBAN_SYSTEM_PROMPT
    assert "不得关闭危急预警、ALERT、SOS" in XIAOBAN_SYSTEM_PROMPT
    assert "真实成功回执" in XIAOBAN_SYSTEM_PROMPT
