import json
import threading
from pathlib import Path


class JsonlMemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def append(self, record: dict) -> dict:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def extend(self, records: list[dict]) -> list[dict]:
        if not records:
            return []
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return records

    def replace_all(self, records: list[dict]) -> list[dict]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return records

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def list(self, *, user_id: str | None = None) -> list[dict]:
        records = self.all()
        if user_id is None:
            return records
        return [record for record in records if record.get("user_id") == user_id]

    def reset(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()
