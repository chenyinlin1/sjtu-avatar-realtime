"""Lightweight visible-context hidden-need inference for skill selection.

This is intentionally simple and local: it does not call an LLM and does not use
any hidden evaluation labels. Replace it with a classifier later if needed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

NEED_DEEP_EMPATHY = "deep_empathy"
NEED_ANALYZE_MOTIVES = "analyze_other_motives"
NEED_ACTION_PLAN = "action_plan"
NEED_VALIDATION = "validation"
NEED_SELF_REFLECTION = "self_reflection"
NEED_PRAISE = "praise_specific_behavior"
NEED_VENT_LISTENING = "vent_listening"
NEED_BALANCED_ANALYSIS = "balanced_analysis"
NEED_UNKNOWN = "unknown"

HIDDEN_NEED_LABELS = {
    NEED_DEEP_EMPATHY,
    NEED_ANALYZE_MOTIVES,
    NEED_ACTION_PLAN,
    NEED_VALIDATION,
    NEED_SELF_REFLECTION,
    NEED_PRAISE,
    NEED_VENT_LISTENING,
    NEED_BALANCED_ANALYSIS,
}

VISIBLE_NEED_RULES = {
    NEED_ANALYZE_MOTIVES: (
        "why", "reason", "motive", "mean", "understand why",
        "\u4e3a\u4ec0\u4e48", "\u539f\u56e0", "\u4ec0\u4e48\u610f\u601d", "\u60f3\u4ec0\u4e48", "\u600e\u4e48\u56de\u4e8b",
    ),
    NEED_ACTION_PLAN: (
        "what should", "how do i", "any advice", "suggest", "plan",
        "\u600e\u4e48\u529e", "\u5efa\u8bae", "\u600e\u4e48\u505a", "\u529e\u6cd5", "\u4e0b\u4e00\u6b65",
    ),
    NEED_DEEP_EMPATHY: (
        "hurt", "sad", "overwhelmed", "exhausted", "alone",
        "\u96be\u53d7", "\u5d29\u6e83", "\u7d2f", "\u59d4\u5c48", "\u6491\u4e0d\u4f4f", "\u6ca1\u4eba\u61c2",
    ),
    NEED_VALIDATION: (
        "am i wrong", "my fault", "right?",
        "\u6211\u9519\u4e86\u5417", "\u662f\u4e0d\u662f\u6211\u7684\u95ee\u9898", "\u6211\u6ca1\u9519", "\u5bf9\u4e0d\u5bf9", "\u602a\u6211",
    ),
    NEED_SELF_REFLECTION: (
        "what can i learn", "reflect", "change myself",
        "\u6210\u957f", "\u53cd\u601d", "\u662f\u4e0d\u662f\u6211",
    ),
    NEED_PRAISE: (
        "i tried", "i did my best",
        "\u52aa\u529b", "\u4ed8\u51fa", "\u505a\u4e86\u8fd9\u4e48\u591a", "\u54ea\u6837\u6ca1", "\u515c\u5e95",
    ),
    NEED_VENT_LISTENING: (
        "just want to talk", "let me vent", "listen",
        "\u8bf4\u8bf4", "\u5410\u69fd", "\u503e\u8bc9", "\u53d1\u6cc4",
    ),
    NEED_BALANCED_ANALYSIS: (
        "both sides", "objectively", "balanced",
        "\u5ba2\u89c2", "\u8fa9\u8bc1", "\u4e24\u8fb9", "\u62c6\u5f00\u770b", "\u5206\u5f00\u770b",
    ),
}


@dataclass(frozen=True)
class HiddenNeedInference:
    need: str
    confidence: str
    evidence: List[str]
    scores: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def sanitize_history(history: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    sanitized: List[Dict[str, str]] = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role = clean(turn.get("role") or turn.get("speaker")).lower()
        normalized_role = "assistant" if role in {"assistant", "npc", "supporter"} else "user"
        content = clean(turn.get("content") or turn.get("text"))
        if content:
            sanitized.append({"role": normalized_role, "content": content})
    return sanitized


def _visible_user_text(history: Iterable[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for turn in sanitize_history(history):
        if turn["role"] == "user":
            parts.append(turn["content"])
    return "\n".join(parts).lower()


def _count_pattern(text: str, pattern: str) -> int:
    if re.search(r"[a-zA-Z]", pattern):
        return len(re.findall(r"\b" + re.escape(pattern.lower()) + r"\b", text))
    return text.count(pattern.lower())


def infer_hidden_need(history: Iterable[Dict[str, Any]]) -> HiddenNeedInference:
    text = _visible_user_text(history)
    scores = {need: 0.0 for need in HIDDEN_NEED_LABELS}
    evidence: List[str] = []
    for need, patterns in VISIBLE_NEED_RULES.items():
        for pattern in patterns:
            hits = _count_pattern(text, pattern)
            if hits:
                scores[need] += float(hits)
                if len(evidence) < 5:
                    evidence.append(pattern)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_need, best_score = ranked[0]
    if best_score <= 0:
        return HiddenNeedInference(NEED_DEEP_EMPATHY, "low", evidence, {**scores, NEED_UNKNOWN: 1.0})

    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score >= 3 and best_score >= second_score + 2:
        confidence = "high"
    elif best_score >= 2:
        confidence = "medium"
    else:
        confidence = "low"
    return HiddenNeedInference(best_need, confidence, evidence, scores)
