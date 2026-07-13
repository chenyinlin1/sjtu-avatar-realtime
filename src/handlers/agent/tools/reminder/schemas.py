"""Validation models and constants for reminder tool calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ReminderOperation(str, Enum):
    CREATE = "create"
    CANCEL = "cancel"


class ReminderRepeat(str, Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"


class ReminderValidationError(ValueError):
    """Raised when a reminder function call is unsafe or incomplete."""


_MEDICATION_PLAN_MUTATION = re.compile(
    r"(改|调整|增加|减少|停|取消|删除).{0,8}"
    r"(剂量|毫克|片|服药次数|用药次数|服药周期|疗程|用药方案|吃药方案)"
    r"|(剂量|服药次数|用药次数|服药周期|疗程|用药方案).{0,8}"
    r"(改|调整|增加|减少|停|取消|删除)"
)


@dataclass(frozen=True)
class CreateReminderArgs:
    title: str
    remind_at: Any
    repeat: ReminderRepeat = ReminderRepeat.NONE
    speak_text: Optional[str] = None


@dataclass(frozen=True)
class CancelReminderArgs:
    entity_id: int
    confirmed: bool


def parse_operation(args: Dict[str, Any]) -> ReminderOperation:
    raw = str(args.get("operation") or "").strip().lower()
    try:
        return ReminderOperation(raw)
    except ValueError as exc:
        raise ReminderValidationError("operation must be create or cancel") from exc


def validate_create_args(args: Dict[str, Any]) -> CreateReminderArgs:
    title = str(args.get("title") or "").strip()
    if not title:
        raise ReminderValidationError("title is required for reminder.create")
    if len(title) > 100:
        raise ReminderValidationError("title must not exceed 100 characters")
    if _MEDICATION_PLAN_MUTATION.search(title):
        raise ReminderValidationError(
            "formal medication plans cannot be changed with the reminder tool; use the family mini program"
        )

    remind_at = args.get("remind_at")
    if remind_at is None or (isinstance(remind_at, str) and not remind_at.strip()):
        raise ReminderValidationError("remind_at is required for reminder.create")

    raw_repeat = str(args.get("repeat") or ReminderRepeat.NONE.value).strip().lower()
    try:
        repeat = ReminderRepeat(raw_repeat)
    except ValueError as exc:
        raise ReminderValidationError("repeat must be none, daily, or weekly") from exc

    raw_speak_text = args.get("speak_text")
    speak_text = str(raw_speak_text).strip() if raw_speak_text is not None else None
    if speak_text == "":
        speak_text = None
    if speak_text and len(speak_text) > 200:
        raise ReminderValidationError("speak_text must not exceed 200 characters")

    return CreateReminderArgs(
        title=title,
        remind_at=remind_at,
        repeat=repeat,
        speak_text=speak_text,
    )


def validate_cancel_args(args: Dict[str, Any]) -> CancelReminderArgs:
    raw_entity_id = args.get("entity_id")
    if isinstance(raw_entity_id, bool):
        raise ReminderValidationError("entity_id is required for reminder.cancel")
    try:
        entity_id = int(raw_entity_id)
    except (TypeError, ValueError) as exc:
        raise ReminderValidationError(
            "entity_id is required for reminder.cancel"
        ) from exc
    if entity_id <= 0:
        raise ReminderValidationError("entity_id must be a positive reminder ID")
    if args.get("confirmed") is not True:
        raise ReminderValidationError(
            "reminder.cancel requires explicit user confirmation before confirmed=true"
        )
    return CancelReminderArgs(entity_id=entity_id, confirmed=True)
