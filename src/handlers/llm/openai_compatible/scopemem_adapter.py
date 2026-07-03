import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger


MEMORY_SYSTEM_TEMPLATE = """{system_prompt}

Relevant durable memories:
{memory_context}

Use the durable memories only when they are relevant to the current user message.
Do not mention that you are using a memory system."""


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_json_loads(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text or "", flags=re.S)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}


class HashTextEmbedder:
    """Local OpenAvatarChat embedder used by ScopeMemory.search()."""

    def __init__(self, dims: int = 256):
        self.dims = dims

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dims
        for token in _tokenize_for_search(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def _tokenize_for_search(text: str) -> List[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", _normalize_text(text).lower())
    tokens: List[str] = []
    for token in raw_tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.extend(token)
            tokens.extend(token[index:index + 2] for index in range(max(0, len(token) - 1)))
        else:
            tokens.append(token)
    return tokens


class OpenAvatarMemoryLLM:
    """ScopeMem memory LLM adapter backed by OpenAvatarChat's configured client."""

    def __init__(self, *, client, model_name: str):
        self.client = client
        self.model_name = model_name
        self.call_counts: Dict[str, int] = {}
        self.token_counts: Dict[str, Dict[str, int]] = {}

    def extract_session_memories(self, *, speaker_a, speaker_b, timestamp, session_key, chats):
        from scopemem.memory.prompts import SESSION_EXTRACT_PROMPT
        from scopemem.memory.text import normalize_text

        lines = []
        for chat in chats:
            speaker = normalize_text(chat.get("speaker"))
            text = normalize_text(chat.get("text") or chat.get("content"))
            dia_id = normalize_text(chat.get("dia_id"))
            if text:
                lines.append({"speaker": speaker, "text": text, "dia_id": dia_id})
        payload = {
            "speaker_a": speaker_a,
            "speaker_b": speaker_b,
            "timestamp": timestamp,
            "session_key": session_key,
            "conversation_lines": lines,
        }
        response = self._chat_json(
            "joint_session_extract",
            [
                {"role": "system", "content": SESSION_EXTRACT_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        memories = response.get("memories", [])
        return memories if isinstance(memories, list) else []

    def reflect(self, *, stage, speaker_a, speaker_b, source_memories):
        from scopemem.memory.prompts import REFLECTION_PROMPT

        if not source_memories:
            return []
        payload = {
            "stage": stage,
            "speaker_a": speaker_a,
            "speaker_b": speaker_b,
            "source_memories": source_memories,
        }
        response = self._chat_json(
            stage,
            [
                {"role": "system", "content": REFLECTION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        memories = response.get("memories", [])
        return memories if isinstance(memories, list) else []

    def _chat_json(self, stage: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
            )
            self.call_counts[stage] = self.call_counts.get(stage, 0) + 1
            usage = getattr(response, "usage", None)
            counts = self.token_counts.setdefault(stage, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
            for key in counts:
                counts[key] += int(getattr(usage, key, 0) or 0)
            text = response.choices[0].message.content or ""
            return _safe_json_loads(text)
        except Exception as exc:
            logger.warning(f"ScopeMem {stage} LLM call failed: {exc}")
            return {}


class OpenAvatarScopeMemory:
    def __init__(
        self,
        *,
        client,
        model_name: str,
        store_path: str,
        user_name: str = "User",
        assistant_name: str = "Assistant",
        top_k: int = 6,
        memory_max_chars: int = 1600,
        dims: int = 256,
    ):
        _ensure_bundled_scopemem()
        from scopemem import ScopeMemConfig, ScopeMemory

        self.user_name = user_name
        self.assistant_name = assistant_name
        self.user_id = f"{user_name}_0"
        self.assistant_user_id = f"{assistant_name}_0"
        self.top_k = top_k
        self.memory_max_chars = memory_max_chars

        store_path_obj = Path(store_path)
        run_dir = store_path_obj.parent.parent
        store_dir = store_path_obj.parent
        config = ScopeMemConfig.for_sample(
            "openavatarchat",
            run_dir=run_dir,
            store_dir=store_dir,
            jsonl_path=store_path_obj,
            qdrant_path=store_dir / "index",
            collection_prefix="openavatarchat_memory",
            profile="longmemeval_s",
            top_k=max(top_k, 1),
            session_workers=1,
            embedding_dims=dims,
            add_batch_size=8,
            replace_similar_on_add=True,
            enable_canonical_reflection=False,
            enable_inferential_reflection=False,
            enable_cross_session_reflection=False,
            enable_entity_state_reflection=False,
            enable_multidim_reflection=False,
            enable_search_ready_reflection=False,
        )
        self.memory = ScopeMemory(
            config,
            llm=OpenAvatarMemoryLLM(client=client, model_name=model_name),
            embedder=HashTextEmbedder(dims=dims),
        )

    def build_system_prompt(self, system_prompt: Dict[str, str], query: str) -> Dict[str, str]:
        memories = self.search(query)
        if not memories:
            return system_prompt
        lines = []
        used_chars = 0
        for index, item in enumerate(memories, start=1):
            text = _normalize_text(item.get("memory") or item.get("text"))
            if not text:
                continue
            line = f"{index}. {text}"
            if used_chars + len(line) + 1 > self.memory_max_chars:
                break
            lines.append(line)
            used_chars += len(line) + 1
        if not lines:
            return system_prompt
        return {
            "role": system_prompt.get("role", "system"),
            "content": MEMORY_SYSTEM_TEMPLATE.format(
                system_prompt=system_prompt.get("content", ""),
                memory_context="\n".join(lines),
            ),
        }

    def remember_turn(self, user_text: str, assistant_text: str) -> None:
        user_text = _normalize_text(user_text)
        assistant_text = _normalize_text(assistant_text)
        if not user_text or not assistant_text:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        messages = [
            {
                "role": "user",
                "speaker": self.user_name,
                "text": user_text,
                "timestamp": timestamp,
                "session_key": "openavatarchat",
                "dia_id": "user",
                "user_id": self.user_id,
            },
            {
                "role": "assistant",
                "speaker": self.assistant_name,
                "text": assistant_text,
                "timestamp": timestamp,
                "session_key": "openavatarchat",
                "dia_id": "assistant",
                "user_id": self.assistant_user_id,
            },
        ]
        self.memory.add(
            messages,
            metadata={"speaker_a": self.user_name, "speaker_b": self.assistant_name, "timestamp": timestamp},
            parallel=False,
        )

    def search(self, query: str) -> List[Dict[str, Any]]:
        results = []
        for user_id, limit in ((self.user_id, self.top_k), (self.assistant_user_id, max(1, self.top_k // 2))):
            try:
                results.extend(self.memory.search(query, user_id=user_id, top_k=limit)["results"])
            except Exception as exc:
                logger.warning(f"ScopeMemory.search failed for {user_id}: {exc}")
        seen = set()
        unique = []
        for item in sorted(results, key=lambda value: value.get("score", 0.0), reverse=True):
            key = _normalize_text(item.get("memory") or item.get("text")).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique[: self.top_k]

    def close(self) -> None:
        self.memory.close()


def _ensure_bundled_scopemem() -> None:
    handlers_dir = Path(__file__).resolve().parents[2]
    if not (handlers_dir / "scopemem").exists():
        raise FileNotFoundError(f"Bundled ScopeMem core does not exist: {handlers_dir / 'scopemem'}")
    value = str(handlers_dir)
    if value not in sys.path:
        sys.path.insert(0, value)
