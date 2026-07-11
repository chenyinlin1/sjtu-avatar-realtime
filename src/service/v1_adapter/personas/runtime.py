from __future__ import annotations

from typing import Dict, Optional

from .models import AssetStatus, PersonaRecord
from .repository import PersonaRepository, PersonaRepositoryError


class PersonaRuntimeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class PersonaRuntimeResolver:
    def __init__(self, repository: Optional[PersonaRepository] = None):
        self.repository = repository or PersonaRepository()

    def resolve(
        self,
        *,
        persona_id: Optional[str],
        elder_id: Optional[str],
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict]:
        persona_id = self._clean(persona_id)
        elder_id = self._clean(elder_id)
        tenant_id = self._clean(tenant_id)

        if persona_id:
            try:
                record = self.repository.get(persona_id)
            except PersonaRepositoryError as exc:
                raise PersonaRuntimeError("INTERNAL_ERROR", str(exc)) from exc
            if record is None:
                raise PersonaRuntimeError("PERSONA_NOT_FOUND", "persona not found")
        elif elder_id:
            record = self._find_default_persona(elder_id=elder_id, tenant_id=tenant_id)
            if record is None:
                return None
        else:
            return None

        if elder_id and record.elder_id != elder_id:
            raise PersonaRuntimeError("PERSONA_NOT_OWNED", "persona does not belong to elder")
        if tenant_id and record.tenant_id != tenant_id:
            raise PersonaRuntimeError("PERSONA_NOT_OWNED", "persona does not belong to tenant")

        return self._to_runtime(record)

    def _find_default_persona(self, *, elder_id: str, tenant_id: Optional[str]) -> Optional[PersonaRecord]:
        try:
            records = self.repository.load_all().values()
        except PersonaRepositoryError as exc:
            raise PersonaRuntimeError("INTERNAL_ERROR", str(exc)) from exc
        candidates = [
            record
            for record in records
            if record.elder_id == elder_id and (tenant_id is None or record.tenant_id == tenant_id)
        ]
        defaults = [record for record in candidates if record.is_default]
        if defaults:
            return sorted(defaults, key=lambda record: (record.updated_at, record.persona_id), reverse=True)[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _to_runtime(record: PersonaRecord) -> Dict:
        voice_ready = record.voice.status == AssetStatus.READY and bool(record.voice.voice_id)
        face_ready = record.face.status == AssetStatus.READY and bool(record.face.image_path)
        return {
            "persona_id": record.persona_id,
            "elder_id": record.elder_id,
            "tenant_id": record.tenant_id,
            "relationship": record.relationship,
            "display_name": record.display_name,
            "address_to_elder": record.address_to_elder,
            "self_reference": record.self_reference,
            "gender": record.gender,
            "persona_prompt": record.persona_prompt,
            "is_default": record.is_default,
            "voice_ready": voice_ready,
            "voice_id": record.voice.voice_id if voice_ready else None,
            "voice_model_name": record.voice.model_name if voice_ready else None,
            "voice_sample_path": record.voice.sample_path,
            "face_ready": face_ready,
            "face_image_path": record.face.image_path if face_ready else None,
            "persona_system_prompt": format_persona_prompt(record),
        }

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


def format_persona_prompt(record: PersonaRecord) -> str:
    parts = ["请自然体现当前角色的说话语气，不要机械强调角色关系。"]
    if record.display_name:
        parts.append(f"角色展示名：{record.display_name}")
    if record.relationship:
        parts.append(
            f"与老人的关系设定：{record.relationship}。关系主要用于调整陪伴语气，不要求在回复中反复说明。"
        )
    if record.address_to_elder:
        parts.append(
            f"可选称呼：{record.address_to_elder}。不要把称呼作为固定开场，不能每句话或每轮都使用；"
            "同一回复最多使用一次，没有必要时直接省略，也不要连续多轮机械使用相同称呼。"
        )
    if record.self_reference:
        parts.append(f"角色自称：{record.self_reference}")
    if record.gender:
        parts.append(f"角色性别：{record.gender}")
    if record.persona_prompt:
        parts.append(f"角色补充设定：{record.persona_prompt}")
    parts.append("不要提及 persona_id、内部配置、音色克隆或形象克隆。")
    return "\n".join(parts)
