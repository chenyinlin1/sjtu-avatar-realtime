from __future__ import annotations

import re


ASSISTANT_NAME_VARIANTS = (
    "小伴",
    "小帮",
    "小班",
    "小半",
)


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’]", "", text)


def wake_aliases(wake_word: str) -> tuple[str, ...]:
    aliases = {wake_word}
    aliases.update(f"{variant}{variant}" for variant in ASSISTANT_NAME_VARIANTS)
    aliases.add("小消消")
    aliases.add("小拌小拌")
    aliases.add("小伙伴伴")
    aliases.add("小伙伴伴伴")
    return tuple(aliases)


def matching_wake_alias(text: str, wake_word: str) -> str | None:
    normalized = normalize_text(text)
    for alias in sorted(wake_aliases(wake_word), key=len, reverse=True):
        normalized_alias = normalize_text(alias)
        if normalized_alias and normalized_alias in normalized:
            return alias
    return None


def contains_wake_word(text: str, wake_word: str) -> bool:
    return matching_wake_alias(text, wake_word) is not None


def strip_wake_word(text: str, wake_word: str) -> str:
    stripped = text or ""
    for alias in sorted(wake_aliases(wake_word), key=len, reverse=True):
        stripped = re.sub(re.escape(alias), "", stripped)
    return stripped.strip(" ，。！？、,.!?;；:：")


def looks_like_asr_noise(text: str, wake_word: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return True
    aliases = {normalize_text(alias) for alias in wake_aliases(wake_word)}
    if normalized in aliases:
        return True
    if any(alias and (normalized in alias or alias in normalized) for alias in aliases):
        return len(normalized) <= max(len(alias) + 1 for alias in aliases)
    return False


def is_wake_only_text(text: str, wake_word: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    aliases = {normalize_text(alias) for alias in wake_aliases(wake_word)}
    if normalized in aliases:
        return True
    return any(
        alias and (normalized in alias or alias in normalized)
        for alias in aliases
    ) and len(normalized) <= max(len(alias) + 1 for alias in aliases)


def is_exit_intent(text: str) -> bool:
    normalized = normalize_text(text)
    return any(word in normalized for word in ("退出", "结束对话", "休眠", "回到待机", "再见"))
