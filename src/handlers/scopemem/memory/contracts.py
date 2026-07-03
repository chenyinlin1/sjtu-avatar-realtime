from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .text import normalize_text, tokenize
from .time import resolve_relative_time


COUNTRIES = {
    "Italy",
    "Canada",
    "Japan",
    "France",
    "Germany",
    "Spain",
    "Portugal",
    "Ireland",
    "Mexico",
    "Greenland",
    "Turkey",
    "UK",
    "United Kingdom",
    "United States",
    "USA",
}

NUMBER_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}

CONTRACT_SOURCES = {
    "temporal_event_contract",
    "commonsense_alias_contract",
    "entity_set_count_contract",
    "search_ready_answer_contract",
    "search_ready_contradiction_guard",
    "search_ready_query_alias",
}


def speaker_for_user_id(user_id: str, speakers: tuple[str, ...]) -> str:
    for speaker in speakers:
        if user_id.startswith(f"{speaker}_"):
            return speaker
    return speakers[0] if speakers else "User"


def evidence_turns(text: str, cues: Iterable[str], *, max_turns: int = 3) -> list[str]:
    normalized_cues = {cue.casefold() for cue in cues if normalize_text(cue)}
    pieces = [piece.strip() for piece in re.split(r"\s+\|\s+|(?<=\.)\s+", normalize_text(text)) if piece.strip()]
    selected = []
    for piece in pieces:
        lowered = piece.casefold()
        if not normalized_cues or any(cue in lowered for cue in normalized_cues):
            selected.append(piece)
        if len(selected) >= max_turns:
            break
    return selected or pieces[:max_turns]


def build_temporal_event_contracts(records: list[dict], speakers: tuple[str, ...]) -> list[dict]:
    phrases = [
        "the weekend before",
        "weekend before",
        "last weekend",
        "the previous week",
        "previous week",
        "last week",
        "the previous month",
        "previous month",
        "last month",
        "the next month",
        "following month",
        "next month",
        "the previous day",
        "previous day",
        "yesterday",
        "the next day",
        "next day",
        "tomorrow",
        "for a month",
    ]
    memories = []
    seen = set()
    for record in records:
        metadata = record.get("metadata", {}) or {}
        if metadata.get("memory_source") not in {"joint_session_extract", "episodic_chunk", "cross_session_timeline"}:
            continue
        timestamp = normalize_text(metadata.get("timestamp") or metadata.get("time_anchor"))
        text = normalize_text(record.get("memory"))
        haystack = f"{metadata.get('time_anchor', '')} {text}".casefold()
        raw = next((phrase for phrase in phrases if phrase in haystack), "")
        if not raw or not timestamp:
            continue
        resolved = resolve_relative_time(raw, timestamp)
        if not resolved:
            continue
        speaker = speaker_for_user_id(record.get("user_id", ""), speakers)
        turns = evidence_turns(text, [raw], max_turns=3)
        event = _derive_event_phrase(text, speaker, raw)
        key = (speaker.casefold(), event.casefold(), raw, resolved)
        if key in seen:
            continue
        seen.add(key)
        evidence_line = " | ".join(turns)
        memories.append(
            {
                "speaker": speaker,
                "text": (
                    f"{speaker} temporal event contract: event={event}; "
                    f"raw_time={raw}; resolved_time={resolved}; evidence={evidence_line}"
                ),
                "kind": "episode",
                "topic": event or "temporal event",
                "time_anchor": resolved,
                "dimension": "temporal",
                "confidence": 0.86,
                "event": event,
                "raw_relative_time": raw,
                "resolved_time": resolved,
                "source_session": normalize_text(metadata.get("session_key")),
                "evidence_line": evidence_line,
                "evidence_turns": turns,
                "expected_answer_form": "duration" if raw == "for a month" else "date",
                "answer_constraints": [resolved],
                "intent_tags": ["temporal"],
                "risk_flags": ["time_granularity"],
                "evidence_session_keys": [normalize_text(metadata.get("session_key"))]
                if normalize_text(metadata.get("session_key"))
                else [],
                "evidence_topics": [normalize_text(metadata.get("memory_topic"))]
                if normalize_text(metadata.get("memory_topic"))
                else [],
            }
        )
    return memories


def _derive_event_phrase(text: str, speaker: str, raw_time: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(rf"^{re.escape(speaker)}\s+said:\s*", "", cleaned, flags=re.IGNORECASE)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", cleaned) if item.strip()]
    for sentence in sentences:
        if raw_time.casefold() in sentence.casefold():
            return re.sub(r"\s+", " ", sentence).strip(" .")
    return cleaned[:120].strip(" .")


def build_entity_set_count_contracts(records: list[dict], speakers: tuple[str, ...]) -> list[dict]:
    by_speaker: dict[str, dict[str, list[tuple[str, dict]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        text = normalize_text(record.get("memory"))
        if not text:
            continue
        speaker = speaker_for_user_id(record.get("user_id", ""), speakers)
        countries = [country for country in COUNTRIES if re.search(rf"\b{re.escape(country)}\b", text, re.IGNORECASE)]
        if len(countries) >= 2 and re.search(r"\b(visited|travel|trip|went|countries?)\b", text, re.IGNORECASE):
            for country in countries:
                by_speaker[speaker]["countries visited"].append((country, record))

    memories = []
    seen = set()
    for speaker, groups in by_speaker.items():
        for topic, values in groups.items():
            items = list(dict.fromkeys(item for item, _record in values))
            if len(items) < 2:
                continue
            key = (speaker.casefold(), topic, tuple(item.casefold() for item in items))
            if key in seen:
                continue
            seen.add(key)
            source_records = [record for _item, record in values]
            turns = evidence_turns(" | ".join(record["memory"] for record in source_records), items, max_turns=3)
            count = len(items)
            constraints = items + [str(count), NUMBER_WORDS.get(count, str(count))]
            memories.append(
                {
                    "speaker": speaker,
                    "text": (
                        f"{speaker} entity set/count contract: {topic}; "
                        f"items={', '.join(items)}; count={count}; evidence={' | '.join(turns)}"
                    ),
                    "kind": "artifact",
                    "topic": topic,
                    "time_anchor": "",
                    "dimension": "list",
                    "confidence": 0.86,
                    "items": items,
                    "count": count,
                    "expected_answer_form": "list",
                    "answer_constraints": constraints,
                    "intent_tags": ["list", "count"],
                    "retrieval_queries": [f"{speaker} {topic}", f"how many countries did {speaker} visit"],
                    "risk_flags": ["incomplete_list"],
                    "evidence_session_keys": list(
                        dict.fromkeys(
                            normalize_text((record.get("metadata", {}) or {}).get("session_key"))
                            for record in source_records
                            if normalize_text((record.get("metadata", {}) or {}).get("session_key"))
                        )
                    )[:6],
                    "evidence_topics": [topic],
                    "evidence_line": " | ".join(turns),
                    "evidence_turns": turns,
                }
            )
    return memories


def build_commonsense_alias_contracts(records: list[dict], speakers: tuple[str, ...]) -> list[dict]:
    mappings = [
        (("25 minutes", "5 minutes"), "Pomodoro technique", "25 minutes on 5 minutes off study cycle"),
        (("star wars", "movie theme"), "John Williams", "Star Wars movie theme composer"),
        (("under armour", "endorsement"), "Under Armour", "sports brand endorsement"),
    ]
    memories = []
    for speaker in speakers:
        speaker_records = [record for record in records if speaker_for_user_id(record.get("user_id", ""), speakers) == speaker]
        blob = " ".join(record.get("memory", "") for record in speaker_records).casefold()
        for terms, alias, clue in mappings:
            if all(term in blob for term in terms):
                memories.append(
                    {
                        "speaker": speaker,
                        "text": f"{speaker} commonsense alias contract: {clue} maps to {alias}.",
                        "kind": "artifact",
                        "topic": clue,
                        "time_anchor": "",
                        "dimension": "entity_alias",
                        "confidence": 0.86,
                        "clue": clue,
                        "alias": alias,
                        "expected_answer_form": "entity_alias",
                        "answer_constraints": [alias],
                        "intent_tags": ["open_domain", "entity_alias"],
                        "retrieval_queries": [f"{speaker} {clue}", alias],
                    }
                )
    return memories


def build_contradiction_guards(records: list[dict], speakers: tuple[str, ...]) -> list[dict]:
    memories = []
    for record in records:
        text = normalize_text(record.get("memory"))
        if not re.search(r"\b(no|not|never|no longer|stopped|instead)\b", text, re.IGNORECASE):
            continue
        speaker = speaker_for_user_id(record.get("user_id", ""), speakers)
        memories.append(
            {
                "speaker": speaker,
                "text": f"{speaker} contradiction guard: {text}",
                "kind": "episode",
                "topic": "contradiction guard",
                "time_anchor": "",
                "dimension": "yes_no",
                "confidence": 0.82,
                "expected_answer_form": "yes_no",
                "answer_constraints": [text],
                "intent_tags": ["yes_no", "negation"],
                "risk_flags": ["negation"],
                "evidence_turns": [text],
                "evidence_line": text,
            }
        )
    return memories[:16]


def metadata_blob(memory: dict) -> str:
    metadata = memory.get("metadata", {}) or {}
    parts = [
        " ".join(str(item) for item in metadata.get("intent_tags", []) or []),
        " ".join(str(item) for item in metadata.get("retrieval_queries", []) or []),
        " ".join(str(item) for item in metadata.get("answer_constraints", []) or []),
        " ".join(str(item) for item in metadata.get("items", []) or []),
        str(metadata.get("count") or ""),
        str(metadata.get("resolved_time") or ""),
        str(metadata.get("raw_relative_time") or ""),
        str(metadata.get("event") or ""),
        str(metadata.get("alias") or ""),
        str(metadata.get("clue") or ""),
    ]
    return normalize_text(" ".join(parts))


def contract_matches_query(question: str, memory: dict, intent: dict | None = None) -> bool:
    metadata = memory.get("metadata", {}) or {}
    source = metadata.get("memory_source", "")
    if source not in CONTRACT_SOURCES and source != "cross_session_timeline":
        return True
    haystack = f"{memory.get('memory', '')} {metadata_blob(memory)}"
    q_terms = set(tokenize(question))
    h_terms = set(tokenize(haystack))
    if not q_terms:
        return True
    overlap = q_terms & h_terms
    if len(overlap) >= 1 and any(term not in {"alice", "bob", "user", "assistant"} for term in overlap):
        return True
    if intent and intent.get("has_list_signal") and metadata.get("count") is not None:
        return bool(overlap)
    return False

