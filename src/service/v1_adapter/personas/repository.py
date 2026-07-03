from __future__ import annotations

import json
import os
import threading
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, List, Optional

from engine_utils.directory_info import DirectoryInfo

from .models import PersonaRecord


def default_storage_root() -> Path:
    configured = os.getenv("V1_PERSONA_STORAGE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(DirectoryInfo.get_project_dir()) / "runtime" / "v1_personas").resolve()


class PersonaRepositoryError(Exception):
    pass


class PersonaRepository:
    def __init__(self, storage_root: Optional[Path] = None):
        self.storage_root = Path(storage_root).expanduser().resolve() if storage_root else default_storage_root()
        self.metadata_path = self.storage_root / "personas.json"
        self._lock = threading.RLock()

    def load_all(self) -> Dict[str, PersonaRecord]:
        with self._lock:
            if not self.metadata_path.exists():
                return {}
            try:
                raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, JSONDecodeError) as exc:
                raise PersonaRepositoryError(f"failed to load persona metadata: {exc}") from exc
            if not isinstance(raw, dict):
                raise PersonaRepositoryError("persona metadata must be a JSON object")
            return {persona_id: PersonaRecord.model_validate(value) for persona_id, value in raw.items()}

    def write_all(self, records: Dict[str, PersonaRecord]) -> None:
        with self._lock:
            self.storage_root.mkdir(parents=True, exist_ok=True)
            payload = {
                persona_id: record.model_dump(mode="json")
                for persona_id, record in sorted(records.items(), key=lambda item: item[0])
            }
            temp_path = self.metadata_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.metadata_path)

    def get(self, persona_id: str) -> Optional[PersonaRecord]:
        return self.load_all().get(persona_id)

    def list_by_owner(self, *, elder_id: str, tenant_id: str) -> List[PersonaRecord]:
        items = [
            record
            for record in self.load_all().values()
            if record.elder_id == elder_id and record.tenant_id == tenant_id
        ]
        return sorted(items, key=lambda record: (record.created_at, record.persona_id))
