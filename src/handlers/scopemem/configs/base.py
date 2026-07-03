import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scopemem.profiles import normalize_profile_name, profile_defaults


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    cleaned = cleaned.replace("-", "_")
    return cleaned[:96] or "sample"



@dataclass(frozen=True)
class ScopeMemConfig:
    sample_id: str
    run_dir: Path
    store_dir: Path
    jsonl_path: Path
    qdrant_path: Path
    collection_name: str
    sample_index: int = 0
    profile: str = "balanced"
    top_k: int = 30
    search_candidate_k: int = 64
    answer_top_k: int = 18
    session_workers: int = 8
    add_batch_size: int = 16
    embedding_dims: int = 256
    store_episodic_chunks: bool = True
    enable_canonical_reflection: bool = True
    enable_inferential_reflection: bool = True
    enable_cross_session_reflection: bool = True
    enable_entity_state_reflection: bool = True
    enable_multidim_reflection: bool = True
    enable_search_ready_reflection: bool = False
    enable_answer_contract_reflection: bool = False
    enable_contradiction_guard_reflection: bool = False
    enable_temporal_event_contract: bool = True
    enable_commonsense_alias_contract: bool = True
    enable_entity_set_count_contract: bool = True
    enable_query_variants: bool = True
    enable_contract_injection: bool = True
    enable_hybrid_rerank: bool = True
    replace_similar_on_add: bool = False
    replace_similarity_threshold: float = 0.95
    cross_session_source_limit: int = 120
    reflection_source_text_limit: int = 240

    @classmethod
    def for_sample(
        cls,
        sample_id: str,
        *,
        run_dir: str | Path,
        sample_index: int = 0,
        top_k: int = 30,
        session_workers: int = 8,
        collection_prefix: str = "scopemem",
        profile: str = "balanced",
        **overrides: Any,
    ) -> "ScopeMemConfig":
        run_dir = Path(run_dir)
        store_dir = Path(overrides.pop("store_dir", run_dir / "stores" / str(sample_id)))
        safe_sample_id = safe_name(sample_id)
        collection_name = overrides.pop("collection_name", f"{collection_prefix}_{safe_sample_id}")
        jsonl_path = Path(overrides.pop("jsonl_path", store_dir / "memories.jsonl"))
        qdrant_path = Path(overrides.pop("qdrant_path", store_dir / "qdrant"))
        profile_name = normalize_profile_name(profile)
        resolved = profile_defaults(profile_name)
        resolved.update(overrides)
        return cls(
            sample_id=str(sample_id),
            run_dir=run_dir,
            store_dir=store_dir,
            jsonl_path=jsonl_path,
            qdrant_path=qdrant_path,
            collection_name=collection_name,
            sample_index=sample_index,
            profile=profile_name,
            top_k=top_k,
            session_workers=session_workers,
            **resolved,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "profile": self.profile,
            "collection_name": self.collection_name,
            "jsonl_path": str(self.jsonl_path),
            "qdrant_path": str(self.qdrant_path),
            "top_k": self.top_k,
            "search_candidate_k": self.search_candidate_k,
            "answer_top_k": self.answer_top_k,
            "embedding_dims": self.embedding_dims,
            "replace_similar_on_add": self.replace_similar_on_add,
            "replace_similarity_threshold": self.replace_similarity_threshold,
        }
