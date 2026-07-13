"""Timezone-aware reminder time normalization.

The LLM is expected to use the injected current time and preferably pass an ISO
timestamp.  The deterministic parser also accepts the common Chinese calendar
and relative expressions used by the speaker, so validation remains testable and
does not reinterpret local wall-clock time as server UTC.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


class ReminderTimeError(ValueError):
    """Base error for invalid reminder times."""


class AmbiguousReminderTimeError(ReminderTimeError):
    """The expression does not contain enough information to schedule safely."""


class PastReminderTimeError(ReminderTimeError):
    """The normalized reminder time has already passed."""


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def resolve_timezone(timezone_name: Optional[str]) -> tuple[ZoneInfo, str]:
    """Return a valid IANA timezone and its effective name."""
    candidate = str(timezone_name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(candidate), candidate
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE


def normalize_remind_at(
    value: Any,
    *,
    timezone_name: Optional[str],
    now: Optional[datetime] = None,
    repeat: str = "none",
) -> int:
    """Normalize epoch/ISO/natural input to an absolute epoch millisecond value."""
    timezone, _effective_name = resolve_timezone(timezone_name)
    local_now = _coerce_now(now, timezone)

    if isinstance(value, bool):
        raise ReminderTimeError("remind_at must be a timestamp or time expression")
    if isinstance(value, (int, float)):
        timestamp_ms = _normalize_epoch(value)
        return _ensure_future(timestamp_ms, local_now)
    if not isinstance(value, str):
        raise ReminderTimeError("remind_at must be a timestamp or time expression")

    text = re.sub(r"\s+", "", value.strip())
    if not text:
        raise AmbiguousReminderTimeError("remind_at is empty")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return _ensure_future(_normalize_epoch(float(text)), local_now)

    iso_datetime = _parse_iso_datetime(text, timezone)
    if iso_datetime is not None:
        return _ensure_future(int(iso_datetime.timestamp() * 1000), local_now)

    parsed = _parse_natural_datetime(text, local_now, repeat=repeat)
    return _ensure_future(int(parsed.timestamp() * 1000), local_now)


def _coerce_now(now: Optional[datetime], timezone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def _normalize_epoch(value: float | int) -> int:
    try:
        epoch = float(value)
    except (TypeError, ValueError) as exc:
        raise ReminderTimeError("invalid epoch timestamp") from exc
    if epoch <= 0:
        raise ReminderTimeError("remind_at epoch must be positive")
    if epoch < 100_000_000_000:
        epoch *= 1000
    return int(epoch)


def _ensure_future(timestamp_ms: int, now: datetime) -> int:
    if timestamp_ms <= int(now.timestamp() * 1000):
        raise PastReminderTimeError("remind_at must be in the future")
    return timestamp_ms


def _parse_iso_datetime(text: str, timezone: ZoneInfo) -> Optional[datetime]:
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("时", ":").replace("分", "").replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
        raise AmbiguousReminderTimeError("a calendar date without a time is ambiguous")
    if not re.match(r"^\d{4}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{1,2}", normalized):
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReminderTimeError("invalid ISO reminder time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _parse_natural_datetime(text: str, now: datetime, *, repeat: str) -> datetime:
    relative = re.search(
        r"(半|\d+|[零〇一二两三四五六七八九十百]+)(分钟|小时|天)后", text
    )
    if relative:
        amount_text, unit = relative.groups()
        if amount_text == "半":
            if unit != "小时":
                raise ReminderTimeError("半 only applies to hours")
            return now + timedelta(minutes=30)
        amount = _parse_chinese_number(amount_text)
        if amount <= 0:
            raise ReminderTimeError("relative reminder delay must be positive")
        if unit == "分钟":
            return now + timedelta(minutes=amount)
        if unit == "小时":
            return now + timedelta(hours=amount)
        return now + timedelta(days=amount)

    clock = _extract_clock(text)
    if clock is None:
        raise AmbiguousReminderTimeError("a specific reminder time is required")
    hour, minute = clock
    daily = repeat == "daily" or "每天" in text or "每日" in text

    target_date = _extract_target_date(text, now.date())
    if target_date is None:
        if not daily:
            raise AmbiguousReminderTimeError(
                "a date is required for a non-repeating reminder"
            )
        target_date = now.date()

    target = datetime.combine(
        target_date, datetime_time(hour, minute), tzinfo=now.tzinfo
    )
    if daily and target <= now:
        target += timedelta(days=1)
    return target


def _extract_target_date(text: str, today: date) -> Optional[date]:
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    if "今天" in text or "今日" in text:
        return today

    next_week = re.search(r"下周([一二三四五六日天])", text)
    if next_week:
        next_monday = today + timedelta(days=7 - today.weekday())
        return next_monday + timedelta(days=_WEEKDAYS[next_week.group(1)])

    weekday = re.search(r"(?:本周|这周|周|星期|礼拜)([一二三四五六日天])", text)
    if weekday:
        wanted = _WEEKDAYS[weekday.group(1)]
        days = (wanted - today.weekday()) % 7
        return today + timedelta(days=days)
    return None


def _extract_clock(text: str) -> Optional[tuple[int, int]]:
    match = re.search(
        r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜间)?"
        r"([零〇一二两三四五六七八九十\d]{1,3})(?:点|时)"
        r"(半|[零〇一二两三四五六七八九十\d]{1,3}分?)?",
        text,
    )
    if not match:
        return None
    period, hour_text, minute_text = match.groups()
    hour = _parse_chinese_number(hour_text)
    minute = 0
    if minute_text == "半":
        minute = 30
    elif minute_text:
        minute = _parse_chinese_number(minute_text.rstrip("分"))

    if period in {"下午", "傍晚", "晚上", "夜里", "夜间"} and 1 <= hour < 12:
        hour += 12
    elif period == "中午" and 1 <= hour < 11:
        hour += 12
    elif period in {"凌晨", "早上", "早晨", "上午"} and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ReminderTimeError("invalid local clock time")
    return hour, minute


def _parse_chinese_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    if not text:
        raise ReminderTimeError("invalid Chinese number")
    if "百" in text:
        left, right = text.split("百", 1)
        hundreds = _CHINESE_DIGITS.get(left, 1) if left else 1
        return hundreds * 100 + (_parse_chinese_number(right) if right else 0)
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[text]
    try:
        return int("".join(str(_CHINESE_DIGITS[char]) for char in text))
    except (KeyError, ValueError) as exc:
        raise ReminderTimeError(f"invalid Chinese number: {text}") from exc
