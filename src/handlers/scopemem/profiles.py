from __future__ import annotations

from typing import Any


def normalize_profile_name(value: str | None) -> str:
    return str(value or "balanced").strip().replace("-", "_").lower()


_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {
        "store_episodic_chunks": True,
        "enable_canonical_reflection": True,
        "enable_inferential_reflection": True,
        "enable_cross_session_reflection": True,
        "enable_entity_state_reflection": True,
        "enable_multidim_reflection": True,
        "enable_search_ready_reflection": False,
        "enable_answer_contract_reflection": False,
        "enable_contradiction_guard_reflection": False,
        "enable_temporal_event_contract": True,
        "enable_commonsense_alias_contract": True,
        "enable_entity_set_count_contract": True,
    },
    "longmemeval_s": {
        "store_episodic_chunks": True,
        "enable_canonical_reflection": False,
        "enable_inferential_reflection": False,
        "enable_cross_session_reflection": False,
        "enable_entity_state_reflection": False,
        "enable_multidim_reflection": False,
        "enable_search_ready_reflection": False,
        "enable_answer_contract_reflection": False,
        "enable_contradiction_guard_reflection": False,
        "enable_temporal_event_contract": True,
        "enable_commonsense_alias_contract": True,
        "enable_entity_set_count_contract": True,
    },
    "full": {
        "store_episodic_chunks": True,
        "enable_canonical_reflection": True,
        "enable_inferential_reflection": True,
        "enable_cross_session_reflection": True,
        "enable_entity_state_reflection": True,
        "enable_multidim_reflection": True,
        "enable_search_ready_reflection": True,
        "enable_answer_contract_reflection": True,
        "enable_contradiction_guard_reflection": True,
        "enable_temporal_event_contract": True,
        "enable_commonsense_alias_contract": True,
        "enable_entity_set_count_contract": True,
    },
}


def profile_defaults(profile: str | None) -> dict[str, Any]:
    name = normalize_profile_name(profile)
    if name not in _PROFILES:
        known = ", ".join(sorted(_PROFILES))
        raise ValueError(f"Unknown ScopeMem profile: {profile}. Available profiles: {known}")
    return dict(_PROFILES[name])


def available_profiles() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))

