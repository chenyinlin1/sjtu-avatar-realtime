import hashlib
import uuid
from datetime import datetime, timezone

from .jsonl import JsonlMemoryStore
from .qdrant_index import QdrantVectorIndex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class ScopeMemoryRepository:
    def __init__(self, *, store: JsonlMemoryStore, index: QdrantVectorIndex):
        self.store = store
        self.index = index
        existing = self.store.all()
        if existing:
            self.index.upsert(existing)

    def add_memory(self, *, user_id: str, memory: str, metadata: dict | None = None) -> dict:
        record = {
            "id": str(uuid.uuid4()),
            "memory": " ".join(str(memory or "").split()).strip(),
            "hash": _hash_text(memory or ""),
            "metadata": dict(metadata or {}),
            "created_at": _now_iso(),
            "updated_at": None,
            "user_id": user_id,
        }
        self.store.append(record)
        self.index.upsert([record])
        return record

    def add_memories(
        self,
        records: list[dict],
        *,
        replace_similar: bool = False,
        similarity_threshold: float = 0.95,
    ) -> list[dict]:
        normalized = []
        for record in records:
            memory = " ".join(str(record.get("memory", "")).split()).strip()
            if not memory:
                continue
            normalized.append(
                {
                    "id": record.get("id") or str(uuid.uuid4()),
                    "memory": memory,
                    "hash": record.get("hash") or _hash_text(memory),
                    "metadata": dict(record.get("metadata", {}) or {}),
                    "created_at": record.get("created_at") or _now_iso(),
                    "updated_at": record.get("updated_at"),
                    "user_id": record["user_id"],
                }
            )
        if replace_similar:
            return self._replace_or_append_similar(normalized, similarity_threshold=similarity_threshold)
        self.store.extend(normalized)
        self.index.upsert(normalized)
        return normalized

    def _replace_or_append_similar(self, records: list[dict], *, similarity_threshold: float) -> list[dict]:
        if not records:
            return []

        stored_by_id = {record["id"]: record for record in self.store.all()}
        output = []
        for record in records:
            match = self._find_similar_record(record, similarity_threshold=similarity_threshold)
            if match:
                record = {
                    **record,
                    "id": match["id"],
                    "created_at": match.get("created_at") or record["created_at"],
                    "updated_at": _now_iso(),
                }
            stored_by_id[record["id"]] = record
            self.index.upsert([record])
            output.append(record)

        self.store.replace_all(list(stored_by_id.values()))
        return output

    def _find_similar_record(self, record: dict, *, similarity_threshold: float) -> dict | None:
        metadata = record.get("metadata", {}) or {}
        filters = {}
        memory_source = metadata.get("memory_source")
        memory_kind = metadata.get("memory_kind")
        if memory_source:
            filters["memory_source"] = memory_source
        if memory_kind:
            filters["memory_kind"] = memory_kind
        candidates = self.search(
            user_id=record["user_id"],
            query=record["memory"],
            top_k=1,
            filters=filters or None,
        )
        if not candidates:
            return None
        best = candidates[0]
        if float(best.get("score", 0.0) or 0.0) < similarity_threshold:
            return None
        return best

    def list_memories(
        self,
        *,
        user_id: str | None = None,
        sources: set[str] | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        records = self.store.list(user_id=user_id)
        if sources is not None:
            records = [
                record
                for record in records
                if (record.get("metadata", {}) or {}).get("memory_source") in sources
            ]
        for key, value in (filters or {}).items():
            records = [
                record
                for record in records
                if (record.get("metadata", {}) or {}).get(key) == value or record.get(key) == value
            ]
        return records

    def search(self, *, user_id: str, query: str, top_k: int, filters: dict | None = None) -> list[dict]:
        return self.index.search(user_id=user_id, query=query, top_k=top_k, filters=filters)

    def reset(self) -> None:
        self.store.reset()
        self.index.reset()

    def close(self) -> None:
        self.index.close()
