from pathlib import Path
from typing import Iterable

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ModuleNotFoundError:  # pragma: no cover - exercised implicitly in lean test envs
    QdrantClient = None
    models = None


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = sum(value * value for value in left[:size]) ** 0.5
    right_norm = sum(value * value for value in right[:size]) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _filter_matches(payload: dict, filters: dict | None) -> bool:
    for key, value in (filters or {}).items():
        if key == "user_id":
            if payload.get("user_id") != value:
                return False
            continue
        if payload.get(key) != value:
            return False
    return True


class QdrantVectorIndex:
    def __init__(self, *, path: str | Path, collection_name: str, embedding_dims: int, embedder):
        self.path = Path(path)
        self.collection_name = collection_name
        self.embedding_dims = embedding_dims
        self.embedder = embedder
        self.path.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = {}
        self._vectors: dict[str, list[float]] = {}
        self.client = QdrantClient(path=str(self.path)) if QdrantClient is not None else None
        if self.client is not None:
            self._ensure_collection()

    def _collection_exists(self) -> bool:
        if self.client is None:
            return True
        if hasattr(self.client, "collection_exists"):
            return bool(self.client.collection_exists(self.collection_name))
        try:
            self.client.get_collection(self.collection_name)
            return True
        except Exception:
            return False

    def _ensure_collection(self) -> None:
        if self.client is None:
            return
        if self._collection_exists():
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=self.embedding_dims, distance=models.Distance.COSINE),
        )

    def upsert(self, records: Iterable[dict]) -> None:
        points = []
        for record in records:
            vector = self.embedder.embed(record.get("memory", ""))
            self._records[record["id"]] = dict(record)
            self._vectors[record["id"]] = vector
            if self.client is None:
                continue
            payload = {
                "data": record.get("memory", ""),
                "memory": record.get("memory", ""),
                "hash": record.get("hash", ""),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "user_id": record.get("user_id"),
                **(record.get("metadata", {}) or {}),
            }
            points.append(models.PointStruct(id=record["id"], vector=vector, payload=payload))
        if points and self.client is not None:
            self._ensure_collection()
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def search(self, *, user_id: str, query: str, top_k: int, filters: dict | None = None) -> list[dict]:
        if self.client is None:
            query_vector = self.embedder.embed(query)
            records = []
            search_filters = {"user_id": user_id, **(filters or {})}
            for record_id, record in self._records.items():
                payload = {"user_id": record.get("user_id"), **(record.get("metadata", {}) or {})}
                if not _filter_matches(payload, search_filters):
                    continue
                item = dict(record)
                item["score"] = float(_cosine(query_vector, self._vectors.get(record_id, [])))
                records.append(item)
            records.sort(key=lambda item: item.get("score", 0.0), reverse=True)
            return records[:top_k]

        self._ensure_collection()
        query_vector = self.embedder.embed(query)
        must = [models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
        for key, value in (filters or {}).items():
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        query_filter = models.Filter(
            must=must
        )
        try:
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )
        except AttributeError:
            result = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )
            hits = result.points

        records = []
        for hit in hits:
            payload = dict(getattr(hit, "payload", {}) or {})
            memory = payload.pop("memory", payload.get("data", ""))
            payload.pop("data", None)
            hash_value = payload.pop("hash", "")
            created_at = payload.pop("created_at", None)
            updated_at = payload.pop("updated_at", None)
            returned_user_id = payload.pop("user_id", user_id)
            records.append(
                {
                    "id": str(hit.id),
                    "memory": memory,
                    "hash": hash_value,
                    "metadata": payload,
                    "score": float(getattr(hit, "score", 0.0) or 0.0),
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "user_id": returned_user_id,
                }
            )
        return records

    def reset(self) -> None:
        self._records.clear()
        self._vectors.clear()
        if self.client is None:
            return
        if self._collection_exists():
            self.client.delete_collection(self.collection_name)
        self._ensure_collection()

    def close(self) -> None:
        if self.client is not None and hasattr(self.client, "close"):
            self.client.close()
