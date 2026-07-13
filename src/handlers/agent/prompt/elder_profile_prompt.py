"""Build the prompt fragment for optional elder-profile context."""
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo


_PROFILE_FIELDS = (
    ("nickname", "昵称"),
    ("gender", "性别"),
    ("age", "年龄"),
    ("native_place", "籍贯"),
)

_USAGE_RULES = (
    "使用规则：这是背景资料，不是当前话题。仅在用户当前问题与称呼、个人情况、"
    "生活建议或地域文化等信息确实相关时自然参考；不要主动复述或刻意引出档案内容，"
    "昵称仅作为身份背景；若当前角色另有对老人的专属称呼，实际称呼优先遵循角色设定。"
    "通常直接回答用户当前问题，非必要不使用称呼；不要每次回复都称呼用户，"
    "也不要形成固定的‘昵称+回答’开头。"
)

_REMINDER_USAGE_RULES = (
    "取消提醒时只能使用下列快照中的明确 entity_id；"
    "若无法唯一定位就先追问，不得猜测。"
    "取消属于破坏性操作，必须先复述具体提醒并取得用户明确确认，"
    "下一轮才能调用取消工具。"
    "此列表只含普通提醒，绝不能据此关闭危急预警、ALERT、SOS 或医疗安全工单。"
)


def build_elder_profile_prompt(device_info: Any) -> str:
    """Return a constrained profile fragment, or an empty string if unavailable."""
    if not isinstance(device_info, Mapping):
        return ""
    profile = device_info.get("elder_profile")
    if not isinstance(profile, Mapping):
        return ""

    details = []
    for field, label in _PROFILE_FIELDS:
        value = profile.get(field)
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            details.append(f"{label}：{cleaned}")
    reminder_lines = _build_reminder_lines(profile, device_info.get("timezone"))
    if not details and not reminder_lines:
        return ""

    lines = []
    if details:
        lines.extend(("【老人档案（仅按需参考）】", _USAGE_RULES, *details))
    if reminder_lines:
        if lines:
            lines.append("")
        lines.extend((
            "【当前提醒快照（仅用于定位取消对象）】",
            _REMINDER_USAGE_RULES,
            *reminder_lines,
        ))
    return "\n".join(lines)


def _build_reminder_lines(profile: Mapping[str, Any], timezone_name: Any) -> list[str]:
    reminders = profile.get("reminders")
    if not isinstance(reminders, list):
        return []
    if not reminders:
        return ["当前快照为空。"]
    try:
        timezone = ZoneInfo(str(timezone_name or "Asia/Shanghai"))
    except Exception:
        timezone = ZoneInfo("Asia/Shanghai")
    lines = []
    for reminder in reminders[:20]:
        if not isinstance(reminder, Mapping):
            continue
        try:
            entity_id = int(reminder.get("id"))
            remind_at = int(reminder.get("remind_at"))
            local_time = datetime.fromtimestamp(remind_at / 1000, timezone)
        except (TypeError, ValueError, OSError):
            continue
        title = str(reminder.get("title") or "").strip()
        if entity_id <= 0 or not title:
            continue
        lines.append(
            f"entity_id={entity_id}｜{title[:100]}｜{local_time.strftime('%Y-%m-%d %H:%M')}"
        )
    return lines
