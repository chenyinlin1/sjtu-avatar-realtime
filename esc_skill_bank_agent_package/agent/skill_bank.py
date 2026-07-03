"""Load SAGE emotional-support skills from a manifest-based skill bank."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SKILL_BANK_DIR = Path(__file__).resolve().parents[1] / "skill_bank"
DEFAULT_FALLBACK_NEED = "deep_empathy"
UNKNOWN_NEED = "unknown"


@dataclass(frozen=True)
class SkillBankEntry:
    skill_id: str
    hidden_need: str
    path: Path
    version: str
    display_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "hidden_need": self.hidden_need,
            "path": str(self.path),
            "version": self.version,
            "display_name": self.display_name,
        }


class SkillBank:
    """A read-only snapshot of skills keyed by hidden need."""

    def __init__(self, bank_dir: Path, manifest: Dict[str, Any], entries: Dict[str, SkillBankEntry]) -> None:
        self.bank_dir = bank_dir
        self.manifest = manifest
        self.entries = entries
        self.version = str(manifest.get("version") or "unknown")

    @classmethod
    def from_dir(cls, bank_dir: Path | str) -> "SkillBank":
        bank_dir = Path(bank_dir)
        manifest_path = bank_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing skill bank manifest: {manifest_path}")
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        entries: Dict[str, SkillBankEntry] = {}
        for row in manifest.get("skills", []):
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or row.get("hidden_need") or "").strip()
            hidden_need = str(row.get("hidden_need") or skill_id).strip()
            rel_path = str(row.get("path") or f"{skill_id}/SKILL.md")
            entries[hidden_need] = SkillBankEntry(
                skill_id=skill_id or hidden_need,
                hidden_need=hidden_need,
                path=bank_dir / rel_path,
                version=str(row.get("version") or manifest.get("version") or "unknown"),
                display_name=str(row.get("display_name") or ""),
            )
        return cls(bank_dir=bank_dir, manifest=manifest, entries=entries)

    def list_entries(self) -> List[SkillBankEntry]:
        return list(self.entries.values())

    def resolve_need(self, inferred_hidden_need: str) -> str:
        need = inferred_hidden_need or UNKNOWN_NEED
        if need in self.entries:
            return need
        if DEFAULT_FALLBACK_NEED in self.entries:
            return DEFAULT_FALLBACK_NEED
        if self.entries:
            return next(iter(self.entries))
        raise KeyError("Skill bank has no entries.")

    def get_entry(self, inferred_hidden_need: str) -> SkillBankEntry:
        return self.entries[self.resolve_need(inferred_hidden_need)]

    def load_skill_text(self, inferred_hidden_need: str) -> str:
        entry = self.get_entry(inferred_hidden_need)
        if not entry.path.exists():
            raise FileNotFoundError(f"Missing skill file: {entry.path}")
        return entry.path.read_text(encoding="utf-8")

    def snapshot_to(self, dest_dir: Path | str) -> Path:
        dest = Path(dest_dir)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.bank_dir, dest)
        return dest


def load_skill_bank(bank_dir: Optional[Path | str] = None) -> SkillBank:
    return SkillBank.from_dir(bank_dir or DEFAULT_SKILL_BANK_DIR)
