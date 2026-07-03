from __future__ import annotations

import re
import time
from collections import Counter

from scopemem.configs import ScopeMemConfig
from scopemem.storage import ScopeMemoryRepository

from .contracts import CONTRACT_SOURCES, contract_matches_query, metadata_blob
from .text import normalize_text, overlap_score, tokenize


QUERY_STOPWORDS = {
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "how",
    "did",
    "does",
    "was",
    "were",
    "the",
    "and",
    "with",
    "from",
    "that",
    "this",
}


def classify_query_intent(query: str) -> dict:
    text = normalize_text(query).casefold()
    has_list = bool(re.search(r"\b(how many|number of|list|which|what .*s\b|count|total|combined)\b", text))
    has_temporal = bool(re.search(r"\b(when|date|day|month|year|before|after|ago|recently|last|next)\b", text))
    has_yes_no = text.startswith(("did ", "does ", "is ", "are ", "was ", "were ", "would ", "could "))
    has_visual = bool(re.search(r"\b(photo|image|picture|visual|shown|looked)\b", text))
    has_open = bool(re.search(r"\b(kind of|type of|called|technique|composer|brand|charity|likely|why)\b", text))
    has_aggregation = bool(re.search(r"\b(how many|how much|total|combined|sum|average|more|less|most|least)\b", text))
    return {
        "has_temporal_signal": has_temporal,
        "expects_temporal_answer": text.startswith(("when ", "what date", "what month", "what year")) or " how long" in text,
        "has_visual_signal": has_visual,
        "has_list_signal": has_list,
        "has_open_domain_signal": has_open,
        "has_yes_no_signal": has_yes_no,
        "has_aggregation_signal": has_aggregation,
        "aggregation_kind": "count" if "how many" in text or "number of" in text else ("sum" if "total" in text else ""),
        "requires_context_overflow": has_list or has_temporal or has_open,
    }


def build_query_variants(question: str) -> list[str]:
    intent = classify_query_intent(question)
    terms = []
    seen = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", normalize_text(question)):
        key = token.casefold()
        if key in QUERY_STOPWORDS or key in seen:
            continue
        seen.add(key)
        terms.append(token)
    core = " ".join(terms[:8])
    variants = [normalize_text(question)]
    if core:
        variants.append(core)
        if intent["has_temporal_signal"]:
            variants.append(f"{core} timeline date")
        if intent["has_visual_signal"]:
            variants.append(f"{core} visual image")
        if intent["has_list_signal"]:
            variants.append(f"{core} list count")
        if intent["has_open_domain_signal"]:
            variants.append(f"{core} alias reason")
    return [item for item in dict.fromkeys(variants) if item]


class ScopeRetriever:
    def __init__(self, *, config: ScopeMemConfig, repository: ScopeMemoryRepository):
        self.config = config
        self.repository = repository
        self.last_debug: dict = {}

    def search(self, query: str, *, user_id: str, top_k: int | None = None, filters: dict | None = None) -> dict:
        started_at = time.time()
        top_k = int(top_k or self.config.answer_top_k)
        intent = classify_query_intent(query)
        variants = build_query_variants(query) if self.config.enable_query_variants else [query]
        merged: dict[str, dict] = {}
        per_query = max(8, self.config.search_candidate_k // max(len(variants), 1))
        speaker = user_id.rsplit("_", 1)[0]
        for variant in variants:
            semantic_query = variant if speaker.casefold() in variant.casefold() else f"{speaker} {variant}"
            for memory in self.repository.search(user_id=user_id, query=semantic_query, top_k=per_query, filters=filters):
                self._merge(merged, memory)
        if self.config.enable_contract_injection:
            for memory in self.repository.list_memories(user_id=user_id, sources=self._contract_sources_for_intent(intent)):
                if contract_matches_query(query, memory, intent):
                    self._merge(merged, memory)
        memories = [memory for memory in merged.values() if contract_matches_query(query, memory, intent)]
        if self.config.enable_hybrid_rerank:
            memories = hybrid_rerank(query, memories, intent)
        else:
            memories.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        results = memories[:top_k]
        self.last_debug = {
            "query_mode": "local",
            "intent": intent,
            "query_variants": variants,
            "selected_source_counts": dict(Counter((item.get("metadata", {}) or {}).get("memory_source", "") for item in results)),
            "search_time": time.time() - started_at,
        }
        return {"results": results, "search_time": time.time() - started_at, "intent": intent}

    def _merge(self, merged: dict[str, dict], memory: dict) -> None:
        key = normalize_text(memory.get("memory", "")).casefold()
        if not key:
            return
        existing = merged.get(key)
        if existing is None or float(memory.get("score", 0.0) or 0.0) > float(existing.get("score", 0.0) or 0.0):
            merged[key] = memory

    def _contract_sources_for_intent(self, intent: dict) -> set[str]:
        sources = set()
        if intent.get("has_temporal_signal"):
            sources.update({"temporal_event_contract", "cross_session_timeline"})
        if intent.get("has_list_signal") or intent.get("has_aggregation_signal"):
            sources.add("entity_set_count_contract")
        if intent.get("has_open_domain_signal"):
            sources.add("commonsense_alias_contract")
        if intent.get("has_yes_no_signal"):
            sources.add("search_ready_contradiction_guard")
        if intent.get("has_temporal_signal") or intent.get("has_list_signal") or intent.get("has_open_domain_signal"):
            sources.update({"search_ready_answer_contract", "search_ready_query_alias"})
        return sources & (CONTRACT_SOURCES | {"cross_session_timeline"})


def hybrid_rerank(query: str, memories: list[dict], intent: dict | None = None) -> list[dict]:
    def score(memory: dict) -> float:
        metadata = memory.get("metadata", {}) or {}
        searchable = f"{memory.get('memory', '')} {metadata_blob(memory)}"
        value = float(memory.get("score", 0.0) or 0.0) * 0.2 + overlap_score(query, searchable)
        source = metadata.get("memory_source", "")
        if source == "joint_session_extract":
            value += 0.6
        if source in CONTRACT_SOURCES:
            value += 0.25
        if intent and intent.get("has_temporal_signal") and source in {"temporal_event_contract", "cross_session_timeline"}:
            value += 0.3
        if intent and intent.get("has_list_signal") and source == "entity_set_count_contract":
            value += 0.35
        return value

    return sorted(memories, key=score, reverse=True)


def select_relevant_memory_ids(question: str, memories: list[dict], *, limit: int) -> list[int]:
    scored = []
    for index, memory in enumerate(memories, start=1):
        searchable = f"{memory.get('memory', '')} {metadata_blob(memory)}"
        source = (memory.get("metadata", {}) or {}).get("memory_source")
        source_bonus = 0.1 if source in CONTRACT_SOURCES else 0.0
        if source == "joint_session_extract":
            source_bonus += 0.6
        scored.append((overlap_score(question, searchable) + source_bonus, index))
    selected = [index for score, index in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0]
    return selected[:limit]


def prioritize_memories(memories: list[dict], selected_ids: list[int]) -> list[dict]:
    selected = []
    used = set()
    for selected_id in selected_ids:
        index = selected_id - 1
        if 0 <= index < len(memories):
            selected.append(memories[index])
            used.add(index)
    return selected + [memory for index, memory in enumerate(memories) if index not in used]
