from __future__ import annotations

import time
from typing import Any

from scopemem.configs import ScopeMemConfig
from scopemem.storage import JsonlMemoryStore, QdrantVectorIndex, ScopeMemoryRepository

from .construction import ScopeConstructor
from .retrieval import ScopeRetriever


class ScopeMemory:
    def __init__(self, config: ScopeMemConfig, *, llm, embedder, repository=None):
        self.config = config
        self.embedder = embedder
        self.llm = llm
        self.repository = repository or ScopeMemoryRepository(
            store=JsonlMemoryStore(config.jsonl_path),
            index=QdrantVectorIndex(
                path=config.qdrant_path,
                collection_name=config.collection_name,
                embedding_dims=config.embedding_dims,
                embedder=self.embedder,
            ),
        )
        self.constructor = ScopeConstructor(config=config, repository=self.repository, llm=self.llm)
        self.retriever = ScopeRetriever(config=config, repository=self.repository)
        self._stats: dict[str, Any] = {
            "add_calls": 0,
            "search_calls": 0,
            "construction_time": 0.0,
            "search_time": 0.0,
        }

    @classmethod
    def from_config(cls, config: ScopeMemConfig | dict[str, Any], *, llm, embedder, repository=None) -> "ScopeMemory":
        if not isinstance(config, ScopeMemConfig):
            config = ScopeMemConfig(**config)
        return cls(config, llm=llm, embedder=embedder, repository=repository)

    def add(self, messages, *, user_id: str | None = None, metadata: dict | None = None, parallel: bool = True) -> dict:
        result = self.constructor.add(messages, user_id=user_id, metadata=metadata, parallel=parallel)
        self._stats["add_calls"] += 1
        self._stats["construction_time"] += float(result.get("construction_time", 0.0) or 0.0)
        return result

    def search(self, query: str, *, user_id: str, top_k: int | None = None, filters: dict | None = None) -> dict:
        started = time.time()
        result = self.retriever.search(query, user_id=user_id, top_k=top_k or self.config.top_k, filters=filters)
        elapsed = time.time() - started
        self._stats["search_calls"] += 1
        self._stats["search_time"] += elapsed
        return {"results": result["results"]}

    def reset(self) -> None:
        self.repository.reset()
        self.constructor._seen.clear()

    def close(self) -> None:
        self.repository.close()

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "config": self.config.public_dict(),
            "llm_call_counts": dict(getattr(self.llm, "call_counts", {}) or {}),
            "llm_token_counts": dict(getattr(self.llm, "token_counts", {}) or {}),
            "num_memories": len(self.repository.list_memories()),
        }
