"""Unified create/cancel reminder function-call tool."""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

from loguru import logger

from handlers.agent.tools.base_tool import BaseTool, ToolResult
from handlers.agent.tools.reminder.action_builder import (
    build_cancel_action,
    build_create_action,
)
from handlers.agent.tools.reminder.pending_actions import get_pending_action_registry
from handlers.agent.tools.reminder.prompt_rules import REMINDER_TOOL_DESCRIPTION
from handlers.agent.tools.reminder.schemas import (
    ReminderOperation,
    ReminderValidationError,
    parse_operation,
    validate_cancel_args,
    validate_create_args,
)
from handlers.agent.tools.reminder.time_utils import (
    ReminderTimeError,
    normalize_remind_at,
    resolve_timezone,
)


class ManageReminderTool(BaseTool):
    """Prepare reminder client actions; fulfillment stays in the Agent orchestrator."""

    def __init__(self, *, context: Any = None):
        self._context = context
        self._shared_states = getattr(context, "shared_states", None)
        self._session_id = str(getattr(context, "session_id", None) or "unknown")
        self._registry = get_pending_action_registry(
            self._shared_states,
            self._session_id,
            create=True,
        )

    @property
    def name(self) -> str:
        return "manage_reminder"

    @property
    def category(self) -> str:
        return "reminder"

    @property
    def dangerous(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return REMINDER_TOOL_DESCRIPTION

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create", "cancel"],
                    "description": "创建提醒或取消提醒。",
                },
                "title": {
                    "type": "string",
                    "description": "operation=create 时必填的普通提醒事项。",
                },
                "remind_at": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                    "description": (
                        "operation=create 时必填；明确的 ISO/epoch 毫秒"
                        "或完整自然语言时间。"
                    ),
                },
                "repeat": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly"],
                    "description": "重复方式，默认 none。",
                },
                "speak_text": {
                    "type": "string",
                    "description": "可选的到点播报内容，不是本轮最终回复。",
                },
                "entity_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "operation=cancel 时必填；必须来自当前提醒快照。",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "仅在用户已明确确认取消后传 true；首次取消请求不得直接设 true。"
                    ),
                },
            },
            "required": ["operation"],
            "additionalProperties": False,
        }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        try:
            operation = parse_operation(args)
            if operation is ReminderOperation.CREATE:
                return self._prepare_create(args)
            return self._prepare_cancel(args)
        except (ReminderValidationError, ReminderTimeError) as exc:
            logger.info(
                f"[{self._session_id}] reminder tool rejected: "
                f"operation={args.get('operation')} error={exc}"
            )
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            logger.opt(exception=exc).warning(
                f"[{self._session_id}] reminder tool preparation failed"
            )
            return ToolResult(
                success=False, error="reminder action could not be prepared"
            )

    def _prepare_create(self, args: Dict[str, Any]) -> ToolResult:
        parsed = validate_create_args(args)
        timezone_name = self._timezone_name()
        remind_at_ms = normalize_remind_at(
            parsed.remind_at,
            timezone_name=timezone_name,
            repeat=parsed.repeat.value,
        )
        action = build_create_action(
            title=parsed.title,
            remind_at_ms=remind_at_ms,
            repeat=parsed.repeat.value,
            speak_text=parsed.speak_text,
        )
        self._registry.register(
            action["action_id"],
            action["type"],
            metadata={"title": parsed.title, "remind_at": remind_at_ms},
        )
        return ToolResult(
            success=True,
            data={
                "status": "pending",
                "operation": "create",
                "action_id": action["action_id"],
                "client_action": action,
                "timezone": timezone_name,
            },
        )

    def _prepare_cancel(self, args: Dict[str, Any]) -> ToolResult:
        parsed = validate_cancel_args(args)
        selected = self._find_current_reminder(parsed.entity_id)
        if selected is None:
            return ToolResult(
                success=False,
                error=(
                    "cannot identify that reminder from elder_profile.reminders; "
                    "ask the user to clarify and do not guess an entity_id"
                ),
            )
        action = build_cancel_action(entity_id=parsed.entity_id)
        self._registry.register(
            action["action_id"],
            action["type"],
            metadata={"entity_id": parsed.entity_id, "reminder": selected},
        )
        return ToolResult(
            success=True,
            data={
                "status": "pending",
                "operation": "cancel",
                "action_id": action["action_id"],
                "client_action": action,
                "reminder": selected,
            },
        )

    def _timezone_name(self) -> str:
        device_info = self._device_info()
        device_timezone = device_info.get("timezone") if device_info else None
        context_timezone = getattr(self._context, "local_time_timezone", None)
        configured = os.getenv("OPENAVATAR_LOCAL_TIMEZONE", "Asia/Shanghai")
        _timezone, effective_name = resolve_timezone(
            device_timezone or context_timezone or configured
        )
        return effective_name

    def _device_info(self) -> Mapping[str, Any]:
        device_info = getattr(self._shared_states, "device_info", None)
        return device_info if isinstance(device_info, Mapping) else {}

    def _find_current_reminder(self, entity_id: int) -> Optional[Dict[str, Any]]:
        profile = self._device_info().get("elder_profile")
        if not isinstance(profile, Mapping):
            return None
        reminders = profile.get("reminders")
        if not isinstance(reminders, list):
            return None
        for reminder in reminders:
            if not isinstance(reminder, Mapping):
                continue
            try:
                reminder_id = int(reminder.get("id"))
            except (TypeError, ValueError):
                continue
            if reminder_id == entity_id:
                return {
                    "id": reminder_id,
                    "title": str(reminder.get("title") or "").strip(),
                    "remind_at": reminder.get("remind_at"),
                }
        return None
