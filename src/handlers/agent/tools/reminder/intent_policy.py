"""Deterministic routing policy for clear reminder creation requests."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from handlers.agent.tools.reminder.prompt_rules import REMINDER_TOOL_MISSING_REPLY


MANAGE_REMINDER_TOOL = "manage_reminder"

_CREATE_INTENT_PATTERNS = (
    re.compile(r"(?:提醒|叫|喊)(?:一下)?我"),
    re.compile(r"(?:帮我|给我)?(?:设|设置|创建|添加|加|定)(?:一个|个)?(?:提醒|闹钟)"),
)
_CANCEL_INTENT = re.compile(
    r"(?:取消|删除|关掉|关闭|停止).{0,8}(?:提醒|闹钟)|(?:别|不要|不用)(?:再)?提醒"
)
_CLOCK_TIME_PATTERNS = (
    re.compile(
        r"(?:\d{1,2}|[零〇一二两三四五六七八九十]+)\s*"
        r"(?:点|时)(?:半|\d{1,2}分?)?"
    ),
    re.compile(r"\d{1,2}\s*[:：]\s*\d{1,2}"),
    re.compile(
        r"(?:半|\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+)\s*"
        r"(?:分钟|小时|天)\s*(?:后|以后|之后)"
    ),
)
_UNSAFE_TARGET = re.compile(r"(?:危急|预警|ALERT|SOS|急救|报警)", re.IGNORECASE)
_FILLER = re.compile(r"[\s，。！？、,.!?的在到请帮给我一下一个个吧呀啊哈]")
_TEMPORAL_CONTEXT = re.compile(
    r"(?:今天|今晚|今早|明天|明早|明晚|后天|每天|每晚|每周[一二三四五六日天]?|上午|下午|晚上|早上|中午|凌晨)"
)


def should_force_create_reminder(text: str) -> bool:
    """Return true only for an explicit create intent with time and subject."""
    normalized = str(text or "").strip()
    if not normalized or _CANCEL_INTENT.search(normalized):
        return False
    if _UNSAFE_TARGET.search(normalized):
        return False
    if not any(pattern.search(normalized) for pattern in _CREATE_INTENT_PATTERNS):
        return False
    if not any(pattern.search(normalized) for pattern in _CLOCK_TIME_PATTERNS):
        return False
    return _has_subject(normalized)


def forced_reminder_tool_choice(
    text: str,
    *,
    available_tools: Iterable[str],
    configured_choice: Any,
) -> Optional[dict]:
    """Build a forced OpenAI tool choice for a clear reminder request."""
    if configured_choice == "none":
        return None
    if MANAGE_REMINDER_TOOL not in set(available_tools):
        return None
    if not should_force_create_reminder(text):
        return None
    return {"type": "function", "function": {"name": MANAGE_REMINDER_TOOL}}


def is_forced_reminder_choice(tool_choice: Any) -> bool:
    if not isinstance(tool_choice, dict):
        return False
    function = tool_choice.get("function")
    return isinstance(function, dict) and function.get("name") == MANAGE_REMINDER_TOOL


def missing_forced_reminder_reply(
    tool_choice: Any,
    *,
    has_tool_calls: bool,
) -> Optional[str]:
    """Return a safe reply when a forced reminder call is ignored by the model."""
    if is_forced_reminder_choice(tool_choice) and not has_tool_calls:
        return REMINDER_TOOL_MISSING_REPLY
    return None


def _has_subject(text: str) -> bool:
    remaining = text
    for pattern in (*_CREATE_INTENT_PATTERNS, *_CLOCK_TIME_PATTERNS):
        remaining = pattern.sub("", remaining)
    remaining = _FILLER.sub("", remaining)
    remaining = _TEMPORAL_CONTEXT.sub("", remaining)
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", remaining))
