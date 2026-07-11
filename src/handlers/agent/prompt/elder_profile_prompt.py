"""Build the prompt fragment for optional elder-profile context."""
from typing import Any, Mapping


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
    if not details:
        return ""

    return "\n".join(("【老人档案（仅按需参考）】", _USAGE_RULES, *details))
