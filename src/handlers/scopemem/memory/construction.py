from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from scopemem.configs import ScopeMemConfig
from scopemem.storage import ScopeMemoryRepository

from .contracts import (
    build_commonsense_alias_contracts,
    build_contradiction_guards,
    build_entity_set_count_contracts,
    build_temporal_event_contracts,
)
from .text import normalize_text
from .time import resolve_relative_time


ALLOWED_KINDS = {"profile", "preference", "relation", "episode", "plan", "artifact"}


def normalize_messages(payload: Any, *, user_id: str | None = None, metadata: dict | None = None) -> list[dict]:
    metadata = metadata or {}
    if isinstance(payload, str):
        payload = [{"role": "user", "content": payload, "user_id": user_id}]
    elif isinstance(payload, dict):
        payload = payload.get("messages", [payload])
    if not isinstance(payload, list):
        raise ValueError("ScopeMemory.add expects a string, a message dict, or a list of message dicts")

    messages = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        role = normalize_text(item.get("role"))
        speaker = normalize_text(item.get("speaker") or item.get("name"))
        if not speaker:
            if role == "assistant":
                speaker = metadata.get("speaker_b", "Assistant")
            else:
                speaker = metadata.get("speaker_a") or (user_id.rsplit("_", 1)[0] if user_id else "User")
        text = normalize_text(item.get("text") or item.get("content") or item.get("message"))
        if not text:
            continue
        messages.append(
            {
                "speaker": speaker,
                "role": role or ("assistant" if speaker == metadata.get("speaker_b") else "user"),
                "text": text,
                "timestamp": normalize_text(
                    item.get("timestamp") or item.get("time_stamp") or item.get("session_time") or metadata.get("timestamp")
                ),
                "session_key": normalize_text(item.get("session_key") or item.get("session_id") or "session_1"),
                "dia_id": normalize_text(item.get("dia_id") or item.get("id") or f"m{index + 1}"),
                "user_id": normalize_text(item.get("user_id") or user_id),
                "has_visual_hint": bool(item.get("has_visual_hint") or item.get("image") or item.get("image_path")),
            }
        )
    return messages


def speakers_from_messages(messages: list[dict], metadata: dict | None = None) -> tuple[str, ...]:
    metadata = metadata or {}
    preferred = [normalize_text(metadata.get("speaker_a")), normalize_text(metadata.get("speaker_b"))]
    speakers = [speaker for speaker in preferred if speaker]
    for message in messages:
        speaker = normalize_text(message.get("speaker"))
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    return tuple(speakers[:2] or ["User"])


def user_ids_for_speakers(messages: list[dict], speakers: tuple[str, ...], *, sample_index: int) -> dict[str, str]:
    output = {}
    for speaker in speakers:
        explicit = next((message.get("user_id") for message in messages if message.get("speaker") == speaker and message.get("user_id")), "")
        output[speaker] = explicit or f"{speaker}_{sample_index}"
    return output


def session_windows(messages: list[dict], *, batch_size: int) -> list[dict]:
    by_key: dict[str, list[dict]] = {}
    timestamps: dict[str, str] = {}
    for message in messages:
        key = message["session_key"]
        by_key.setdefault(key, []).append(message)
        timestamps.setdefault(key, message.get("timestamp", ""))
    windows = []
    for key in sorted(by_key, key=_session_sort_key):
        items = by_key[key]
        for window_index, start in enumerate(range(0, len(items), max(1, batch_size)), start=1):
            chunk = items[start : start + max(1, batch_size)]
            window_key = key if len(items) <= batch_size else f"{key}_chunk_{window_index}"
            windows.append({"session_key": window_key, "timestamp": timestamps.get(key, ""), "chats": chunk})
    return windows


def _session_sort_key(value: str) -> tuple[int, str]:
    parts = value.split("_")
    if parts and parts[-1].isdigit():
        return int(parts[-1]), value
    return 10**9, value


class ScopeConstructor:
    def __init__(self, *, config: ScopeMemConfig, repository: ScopeMemoryRepository, llm):
        self.config = config
        self.repository = repository
        self.llm = llm
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, payload: Any, *, user_id: str | None = None, metadata: dict | None = None, parallel: bool = True) -> dict:
        started_at = time.time()
        messages = normalize_messages(payload, user_id=user_id, metadata=metadata)
        speakers = speakers_from_messages(messages, metadata)
        speaker_ids = user_ids_for_speakers(messages, speakers, sample_index=self.config.sample_index)
        windows = session_windows(messages, batch_size=self.config.add_batch_size)

        if parallel and len(windows) > 1:
            extracted = self._extract_parallel(speakers, windows)
        else:
            extracted = {
                index: self._extract_window(speakers, window)
                for index, window in enumerate(windows)
            }

        for index, window in enumerate(windows):
            self._store_stage(
                speakers=speakers,
                speaker_ids=speaker_ids,
                timestamp=window["timestamp"],
                session_key=window["session_key"],
                source_tag="joint_session_extract",
                memories=extracted.get(index, []),
            )
            if self.config.store_episodic_chunks:
                self._store_stage(
                    speakers=speakers,
                    speaker_ids=speaker_ids,
                    timestamp=window["timestamp"],
                    session_key=window["session_key"],
                    source_tag="episodic_chunk",
                    memories=self._episodic_memories(window),
                )

        self._run_reflections(speakers, speaker_ids)
        self._run_local_contracts(speakers, speaker_ids)

        elapsed = time.time() - started_at
        return {
            "construction_time": elapsed,
            "sessions_processed": len(windows),
            "num_memories": len(self.repository.list_memories()),
            "llm_call_counts": dict(getattr(self.llm, "call_counts", {}) or {}),
            "llm_token_counts": dict(getattr(self.llm, "token_counts", {}) or {}),
        }

    def _extract_parallel(self, speakers: tuple[str, ...], windows: list[dict]) -> dict[int, list[dict]]:
        max_workers = min(max(1, self.config.session_workers), len(windows))
        results: dict[int, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._extract_window, speakers, window): index for index, window in enumerate(windows)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results

    def _extract_window(self, speakers: tuple[str, ...], window: dict) -> list[dict]:
        speaker_a = speakers[0]
        speaker_b = speakers[1] if len(speakers) > 1 else "Assistant"
        return self.llm.extract_session_memories(
            speaker_a=speaker_a,
            speaker_b=speaker_b,
            timestamp=window["timestamp"],
            session_key=window["session_key"],
            chats=window["chats"],
        )

    def _episodic_memories(self, window: dict) -> list[dict]:
        memories = []
        for chat in window["chats"]:
            speaker = chat["speaker"]
            line = f"{speaker} said: {chat['text']}"
            memories.append(
                {
                    "speaker": speaker,
                    "text": line,
                    "kind": "episode",
                    "topic": "conversation",
                    "time_anchor": window.get("timestamp", ""),
                    "dia_ids": [chat["dia_id"]] if chat.get("dia_id") else [],
                    "has_visual_hint": bool(chat.get("has_visual_hint")),
                    "evidence_turns": [line],
                    "evidence_line": line,
                }
            )
        return memories

    def _run_reflections(self, speakers: tuple[str, ...], speaker_ids: dict[str, str]) -> None:
        stage_flags = [
            ("reflective_canonical", self.config.enable_canonical_reflection),
            ("reflective_inference", self.config.enable_inferential_reflection),
            ("cross_session_timeline", self.config.enable_cross_session_reflection),
            ("cross_session_entity_state", self.config.enable_entity_state_reflection),
            ("multi_dimensional_inference", self.config.enable_multidim_reflection),
            ("search_ready_query_alias", self.config.enable_search_ready_reflection),
            ("search_ready_answer_contract", self.config.enable_search_ready_reflection and self.config.enable_answer_contract_reflection),
            ("search_ready_contradiction_guard", self.config.enable_search_ready_reflection and self.config.enable_contradiction_guard_reflection),
        ]
        for stage, enabled in stage_flags:
            if not enabled:
                continue
            source_memories = self._source_memories(limit=self.config.cross_session_source_limit)
            if not source_memories:
                continue
            memories = self.llm.reflect(
                stage=stage,
                speaker_a=speakers[0],
                speaker_b=speakers[1] if len(speakers) > 1 else "Assistant",
                source_memories=source_memories,
            )
            self._store_stage(
                speakers=speakers,
                speaker_ids=speaker_ids,
                timestamp="",
                session_key=f"conversation_{stage}",
                source_tag=stage,
                memories=memories,
            )

    def _run_local_contracts(self, speakers: tuple[str, ...], speaker_ids: dict[str, str]) -> None:
        records = self.repository.list_memories()
        builders = []
        if self.config.enable_temporal_event_contract:
            builders.append(("temporal_event_contract", build_temporal_event_contracts(records, speakers)))
        if self.config.enable_commonsense_alias_contract:
            builders.append(("commonsense_alias_contract", build_commonsense_alias_contracts(records, speakers)))
        if self.config.enable_entity_set_count_contract:
            builders.append(("entity_set_count_contract", build_entity_set_count_contracts(records, speakers)))
        if self.config.enable_search_ready_reflection and self.config.enable_contradiction_guard_reflection:
            builders.append(("search_ready_contradiction_guard", build_contradiction_guards(records, speakers)))
        for source_tag, memories in builders:
            self._store_stage(
                speakers=speakers,
                speaker_ids=speaker_ids,
                timestamp="",
                session_key="conversation_contract_reflection",
                source_tag=source_tag,
                memories=memories,
            )

    def _source_memories(self, *, limit: int) -> list[dict]:
        output = []
        for record in self.repository.list_memories()[-limit:]:
            metadata = record.get("metadata", {}) or {}
            output.append(
                {
                    "speaker": record.get("user_id", "").rsplit("_", 1)[0],
                    "text": record.get("memory", "")[: self.config.reflection_source_text_limit],
                    "kind": metadata.get("memory_kind", "episode"),
                    "topic": metadata.get("memory_topic", "conversation"),
                    "time_anchor": metadata.get("time_anchor", ""),
                    "source": metadata.get("memory_source", ""),
                    "session_key": metadata.get("session_key", ""),
                    "timestamp": metadata.get("timestamp", ""),
                }
            )
        return output

    def _store_stage(
        self,
        *,
        speakers: tuple[str, ...],
        speaker_ids: dict[str, str],
        timestamp: str,
        session_key: str,
        source_tag: str,
        memories: list[dict],
    ) -> None:
        records = []
        for memory in memories or []:
            parsed = self._normalize_memory(memory, speakers, timestamp)
            if not parsed:
                continue
            speaker = parsed["speaker"]
            user_id = speaker_ids.get(speaker, f"{speaker}_{self.config.sample_index}")
            seen_key = (user_id, source_tag, parsed["text"].casefold())
            if source_tag not in {"temporal_event_contract", "commonsense_alias_contract", "entity_set_count_contract"}:
                seen_key = (user_id, "global", parsed["text"].casefold())
            if seen_key in self._seen and not self.config.replace_similar_on_add:
                continue
            self._seen.add(seen_key)
            records.append(
                {
                    "user_id": user_id,
                    "memory": parsed["text"],
                    "metadata": {
                        "timestamp": timestamp,
                        "memory_source": source_tag,
                        "memory_kind": parsed["kind"],
                        "memory_topic": parsed["topic"],
                        "time_anchor": parsed["time_anchor"],
                        "session_key": session_key,
                        "confidence": parsed.get("confidence"),
                        "dia_ids": parsed.get("dia_ids", []),
                        "has_visual_hint": parsed.get("has_visual_hint", False),
                        "memory_dimension": parsed.get("dimension", ""),
                        "evidence_session_keys": parsed.get("evidence_session_keys", []),
                        "evidence_topics": parsed.get("evidence_topics", []),
                        "intent_tags": parsed.get("intent_tags", []),
                        "retrieval_queries": parsed.get("retrieval_queries", []),
                        "expected_answer_form": parsed.get("expected_answer_form", ""),
                        "answer_constraints": parsed.get("answer_constraints", []),
                        "risk_flags": parsed.get("risk_flags", []),
                        "event": parsed.get("event", ""),
                        "raw_relative_time": parsed.get("raw_relative_time", ""),
                        "resolved_time": parsed.get("resolved_time", ""),
                        "source_session": parsed.get("source_session", ""),
                        "evidence_line": parsed.get("evidence_line", ""),
                        "evidence_turns": parsed.get("evidence_turns", []),
                        "items": parsed.get("items", []),
                        "count": parsed.get("count"),
                        "alias": parsed.get("alias", ""),
                        "clue": parsed.get("clue", ""),
                    },
                }
            )
        self.repository.add_memories(
            records,
            replace_similar=self.config.replace_similar_on_add,
            similarity_threshold=self.config.replace_similarity_threshold,
        )

    def _normalize_memory(self, memory: dict, speakers: tuple[str, ...], timestamp: str) -> dict | None:
        text = normalize_text(memory.get("text") or memory.get("memory") or memory.get("fact"))
        if not text:
            return None
        speaker = self._resolve_speaker(memory.get("speaker"), text, speakers)
        if speaker not in speakers:
            return None
        if not text.casefold().startswith(speaker.casefold()):
            text = f"{speaker} {text}"
        kind = normalize_text(memory.get("kind")).casefold()
        if kind not in ALLOWED_KINDS:
            kind = "episode"
        time_anchor = normalize_text(memory.get("time_anchor"))
        resolved_anchor = resolve_relative_time(time_anchor, timestamp) if timestamp else ""
        return {
            **memory,
            "speaker": speaker,
            "text": text,
            "kind": kind,
            "topic": normalize_text(memory.get("topic")) or kind,
            "time_anchor": resolved_anchor or time_anchor,
        }

    def _resolve_speaker(self, raw_speaker: Any, text: str, speakers: tuple[str, ...]) -> str:
        raw = normalize_text(raw_speaker).casefold()
        aliases = {
            "speaker_a": speakers[0] if speakers else "User",
            "speaker 1": speakers[0] if speakers else "User",
            "user": speakers[0] if speakers else "User",
            "speaker_b": speakers[1] if len(speakers) > 1 else (speakers[0] if speakers else "Assistant"),
            "speaker 2": speakers[1] if len(speakers) > 1 else (speakers[0] if speakers else "Assistant"),
            "assistant": speakers[1] if len(speakers) > 1 else (speakers[0] if speakers else "Assistant"),
        }
        if raw in aliases:
            return aliases[raw]
        for speaker in speakers:
            if raw == speaker.casefold() or normalize_text(text).casefold().startswith(speaker.casefold()):
                return speaker
        return speakers[0] if speakers else "User"
