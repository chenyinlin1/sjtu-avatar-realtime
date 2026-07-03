import json
import re
from collections import Counter
from typing import Any


STOPWORDS = {
    "the",
    "and",
    "that",
    "what",
    "when",
    "where",
    "which",
    "with",
    "does",
    "did",
    "was",
    "were",
    "are",
    "you",
    "your",
    "user",
    "assistant",
    "question",
    "time",
}


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def tokenize(value: str) -> list[str]:
    return [
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", normalize_text(value))
        if token.casefold() not in STOPWORDS
    ]


def token_counts(value: str) -> Counter:
    return Counter(tokenize(value))


def safe_json_loads(value: str) -> Any:
    text = normalize_text(value)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                return {}
        start = min([idx for idx in [text.find("{"), text.find("[")] if idx >= 0], default=-1)
        if start >= 0:
            end = max(text.rfind("}"), text.rfind("]"))
            if end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def overlap_score(question: str, memory: str) -> float:
    q_tokens = set(tokenize(question))
    if not q_tokens:
        return 0.0
    m_tokens = set(tokenize(memory))
    return len(q_tokens & m_tokens) / max(len(q_tokens), 1)
