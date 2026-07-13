"""Fulfill reminder tool results through the existing client_action channel."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from loguru import logger

from handlers.agent.tools.reminder.pending_actions import get_pending_action_registry


DEFAULT_ACK_TIMEOUT_SECONDS = 8.0


def is_pending_reminder_result(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get("status") == "pending"
        and isinstance(data.get("client_action"), dict)
        and str(data["client_action"].get("type") or "").startswith("reminder.")
    )


def fulfill_reminder_action(
    *,
    shared_states: Any,
    session_id: str,
    pending_data: Dict[str, Any],
    send_action: Callable[[Dict[str, Any]], None],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Send one prepared action, wait for its real ack, and return structured outcome."""
    action = dict(pending_data["client_action"])
    action_id = str(action.get("action_id") or "")
    action_type = str(action.get("type") or "")
    operation = str(pending_data.get("operation") or action_type.rsplit(".", 1)[-1])
    registry = get_pending_action_registry(shared_states, session_id, create=False)
    if registry is None:
        return _failure_outcome(
            action_id=action_id,
            action_type=action_type,
            operation=operation,
            status="unavailable",
            error="session action registry is unavailable",
            error_code="ACTION_REGISTRY_UNAVAILABLE",
        )

    try:
        send_action(action)
    except Exception:
        logger.warning(
            f"[{session_id}] reminder client_action delivery failed: "
            f"type={action_type} action_id={action_id}"
        )
        ack = registry.abort(
            action_id,
            error="client_action could not be delivered",
            error_code="CLIENT_ACTION_SEND_FAILED",
        )
    else:
        logger.info(
            f"[{session_id}] reminder client_action dispatched: type={action_type} "
            f"action_id={action_id} stage=client_action_sent"
        )
        ack = registry.wait(
            action_id, timeout if timeout is not None else _ack_timeout()
        )
    outcome: Dict[str, Any] = {
        "ok": ack.ok,
        "status": ack.status,
        "operation": operation,
        "action_type": action_type,
        "action_id": action_id,
        "entity_id": ack.entity_id,
        "error": ack.error,
        "error_code": ack.error_code,
    }
    if outcome["ok"] and operation == "create" and ack.entity_id is None:
        outcome.update(
            {
                "ok": False,
                "status": "failed",
                "error": "successful reminder.create ack is missing entity_id",
                "error_code": "ACK_ENTITY_ID_MISSING",
            }
        )
    if outcome["ok"]:
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if operation == "create":
            outcome.update(
                {
                    "title": args.get("title"),
                    "remind_at": args.get("remind_at"),
                    "repeat": args.get("repeat"),
                }
            )
        _apply_snapshot_result(shared_states, action, ack.entity_id)
    logger.info(
        f"[{session_id}] reminder action finalized: type={action_type} action_id={action_id} "
        f"entity_id={ack.entity_id} ok={outcome['ok']} status={outcome['status']} "
        f"error_code={outcome.get('error_code') or '-'} stage=result_processed"
    )
    return outcome


def _failure_outcome(
    *,
    action_id: str,
    action_type: str,
    operation: str,
    status: str,
    error: str,
    error_code: str,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "operation": operation,
        "action_type": action_type,
        "action_id": action_id,
        "entity_id": None,
        "error": error,
        "error_code": error_code,
    }


def _ack_timeout() -> float:
    raw = os.getenv("OPENAVATAR_REMINDER_ACK_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_ACK_TIMEOUT_SECONDS
    try:
        return max(0.1, min(float(raw), 60.0))
    except ValueError:
        return DEFAULT_ACK_TIMEOUT_SECONDS


def _apply_snapshot_result(
    shared_states: Any, action: Dict[str, Any], entity_id: Optional[int]
) -> None:
    """Update only the in-memory session snapshot after a successful real ack."""
    if shared_states is None:
        return
    device_info = getattr(shared_states, "device_info", None)
    if not isinstance(device_info, dict):
        return
    profile = device_info.get("elder_profile")
    if not isinstance(profile, dict):
        profile = {}
        device_info["elder_profile"] = profile
    reminders = profile.get("reminders")
    if not isinstance(reminders, list):
        reminders = []
        profile["reminders"] = reminders

    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    action_type = action.get("type")
    if action_type == "reminder.create" and entity_id is not None:
        reminders[:] = [item for item in reminders if _entity_id(item) != entity_id]
        reminders.append(
            {
                "id": entity_id,
                "title": str(args.get("title") or "").strip(),
                "remind_at": int(args.get("remind_at")),
            }
        )
    elif action_type == "reminder.cancel":
        cancelled_id = _positive_int(args.get("entity_id"))
        if cancelled_id is not None:
            reminders[:] = [
                item for item in reminders if _entity_id(item) != cancelled_id
            ]


def _entity_id(item: Any) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    return _positive_int(item.get("id"))


def _positive_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
