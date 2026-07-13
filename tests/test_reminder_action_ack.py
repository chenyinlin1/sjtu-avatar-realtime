from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from handlers.agent.tools.reminder.fulfillment import fulfill_reminder_action
from handlers.agent.tools.reminder.pending_actions import PendingActionRegistry
from handlers.agent.tools.reminder.time_utils import normalize_remind_at
from handlers.agent.tools.reminder.tool import ManageReminderTool


def test_action_ack_success_failure_and_early_arrival():
    registry = PendingActionRegistry("session-1")
    registry.register("create-1", "reminder.create")

    assert (
        registry.resolve(
            {
                "action_id": "create-1",
                "ok": True,
                "entity_id": 123,
                "error": None,
            }
        )
        == "matched"
    )
    success = registry.wait("create-1", timeout=0.01)
    assert success.ok is True
    assert success.status == "succeeded"
    assert success.entity_id == 123

    registry.register("cancel-1", "reminder.cancel")
    assert (
        registry.resolve(
            {
                "action_id": "cancel-1",
                "ok": False,
                "error": {"code": "BACKEND_BUSY", "message": "try later"},
            }
        )
        == "matched"
    )
    failed = registry.wait("cancel-1", timeout=0.01)
    assert failed.ok is False
    assert failed.status == "failed"
    assert failed.error_code == "BACKEND_BUSY"


def test_action_ack_timeout_duplicate_and_unknown_are_idempotent():
    registry = PendingActionRegistry("session-2")
    registry.register("timeout-1", "reminder.create")

    timed_out = registry.wait("timeout-1", timeout=0.001)
    assert timed_out.ok is False
    assert timed_out.status == "timeout"
    assert timed_out.error_code == "ACK_TIMEOUT"
    assert registry.resolve({"action_id": "timeout-1", "ok": True}) == "duplicate"
    assert registry.resolve({"action_id": "never-registered", "ok": True}) == "unknown"


def test_duplicate_ack_does_not_overwrite_first_result():
    registry = PendingActionRegistry("session-3")
    registry.register("create-2", "reminder.create")

    assert (
        registry.resolve({"action_id": "create-2", "ok": True, "entity_id": 9})
        == "matched"
    )
    assert (
        registry.resolve({"action_id": "create-2", "ok": False, "error": "late"})
        == "duplicate"
    )
    result = registry.wait("create-2", timeout=0.01)
    assert result.ok is True
    assert result.entity_id == 9


def test_session_close_wakes_waiters_and_late_ack_is_ignored():
    registry = PendingActionRegistry("session-4")
    registry.register("cancel-2", "reminder.cancel")
    registry.close()

    result = registry.wait("cancel-2", timeout=0.1)
    assert result.ok is False
    assert result.status == "session_closed"
    assert result.error_code == "SESSION_CLOSED"
    assert registry.resolve({"action_id": "cancel-2", "ok": True}) == "closed"


def make_context():
    shared_states = SimpleNamespace(
        device_info={
            "device_sn": "speaker-1",
            "timezone": "Asia/Shanghai",
            "elder_profile": {"reminders": []},
        }
    )
    return SimpleNamespace(session_id="edge-session", shared_states=shared_states)


def prepare_create(context):
    return ManageReminderTool(context=context).execute(
        {
            "operation": "create",
            "title": "喝水",
            "remind_at": "2099-01-01T08:00:00+08:00",
        }
    )


def test_create_ack_without_entity_id_is_a_protocol_failure():
    context = make_context()
    prepared = prepare_create(context)
    registry = context.shared_states.reminder_pending_actions

    def send_action(action):
        registry.resolve({"action_id": action["action_id"], "ok": True, "error": None})

    outcome = fulfill_reminder_action(
        shared_states=context.shared_states,
        session_id=context.session_id,
        pending_data=prepared.data,
        send_action=send_action,
        timeout=0.01,
    )

    assert outcome["ok"] is False
    assert outcome["error_code"] == "ACK_ENTITY_ID_MISSING"
    assert context.shared_states.device_info["elder_profile"]["reminders"] == []


def test_client_action_send_failure_cleans_pending_without_waiting_for_ack():
    context = make_context()
    prepared = prepare_create(context)
    registry = context.shared_states.reminder_pending_actions

    def send_action(_action):
        raise RuntimeError("channel closed")

    outcome = fulfill_reminder_action(
        shared_states=context.shared_states,
        session_id=context.session_id,
        pending_data=prepared.data,
        send_action=send_action,
        timeout=30,
    )

    assert outcome["ok"] is False
    assert outcome["status"] == "send_failed"
    assert outcome["error_code"] == "CLIENT_ACTION_SEND_FAILED"
    assert registry.pending_count == 0


def test_more_required_chinese_time_forms_are_normalized_in_device_timezone():
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 7, 13, 10, 15, tzinfo=timezone)

    after_tomorrow = normalize_remind_at(
        "后天下午三点", timezone_name="Asia/Shanghai", now=now
    )
    tonight = normalize_remind_at(
        "今天晚上九点", timezone_name="Asia/Shanghai", now=now
    )

    assert datetime.fromtimestamp(after_tomorrow / 1000, timezone) == datetime(
        2026, 7, 15, 15, 0, tzinfo=timezone
    )
    assert datetime.fromtimestamp(tonight / 1000, timezone) == datetime(
        2026, 7, 13, 21, 0, tzinfo=timezone
    )


@pytest.mark.parametrize(
    "arguments,action_type",
    [
        (
            {
                "operation": "create",
                "title": "喝水",
                "remind_at": "2099-01-01T08:00:00+08:00",
            },
            "reminder.create",
        ),
        (
            {"operation": "cancel", "entity_id": 123, "confirmed": True},
            "reminder.cancel",
        ),
    ],
)
def test_backend_failure_ack_is_returned_for_create_and_cancel(arguments, action_type):
    shared_states = SimpleNamespace(
        device_info={
            "timezone": "Asia/Shanghai",
            "elder_profile": {
                "reminders": [
                    {"id": 123, "title": "吃药", "remind_at": 1_800_000_000_000}
                ],
            },
        }
    )
    context = SimpleNamespace(session_id="failure-session", shared_states=shared_states)
    prepared = ManageReminderTool(context=context).execute(arguments)
    registry = shared_states.reminder_pending_actions

    def send_action(action):
        assert action["type"] == action_type
        registry.resolve(
            {
                "action_id": action["action_id"],
                "ok": False,
                "error": {"code": "BACKEND_BUSY", "message": "try later"},
            }
        )

    outcome = fulfill_reminder_action(
        shared_states=shared_states,
        session_id=context.session_id,
        pending_data=prepared.data,
        send_action=send_action,
        timeout=0.01,
    )

    assert outcome["ok"] is False
    assert outcome["status"] == "failed"
    assert outcome["error_code"] == "BACKEND_BUSY"
