from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AssetStatus(str, Enum):
    NONE = "NONE"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class PersonaStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    FAILED = "FAILED"


class VoiceAsset(BaseModel):
    status: AssetStatus = AssetStatus.NONE
    ref_text: Optional[str] = None
    sample_duration_ms: Optional[int] = None
    voice_id: Optional[str] = None
    model_name: Optional[str] = None
    sample_path: Optional[str] = None
    clone_source_url: Optional[str] = None
    fail_reason: Optional[str] = None
    updated_at: Optional[int] = None


class FaceAsset(BaseModel):
    status: AssetStatus = AssetStatus.NONE
    image_path: Optional[str] = None
    fail_reason: Optional[str] = None
    updated_at: Optional[int] = None


class PersonaRecord(BaseModel):
    persona_id: str
    elder_id: str
    tenant_id: str
    relationship: Optional[str] = None
    display_name: str
    address_to_elder: Optional[str] = None
    self_reference: Optional[str] = None
    gender: Optional[str] = None
    persona_prompt: Optional[str] = None
    is_default: bool = False
    voice: VoiceAsset = Field(default_factory=VoiceAsset)
    face: FaceAsset = Field(default_factory=FaceAsset)
    status: PersonaStatus = PersonaStatus.DRAFT
    created_at: int
    updated_at: int


def now_ms() -> int:
    return int(time.time() * 1000)


def create_persona_record(
    *,
    persona_id: str,
    elder_id: str,
    tenant_id: str,
    display_name: str,
    is_default: bool,
    relationship: Optional[str] = None,
    address_to_elder: Optional[str] = None,
    self_reference: Optional[str] = None,
    gender: Optional[str] = None,
    persona_prompt: Optional[str] = None,
) -> PersonaRecord:
    timestamp = now_ms()
    return PersonaRecord(
        persona_id=persona_id,
        elder_id=elder_id,
        tenant_id=tenant_id,
        relationship=relationship,
        display_name=display_name,
        address_to_elder=address_to_elder,
        self_reference=self_reference,
        gender=gender,
        persona_prompt=persona_prompt,
        is_default=is_default,
        created_at=timestamp,
        updated_at=timestamp,
    )


def recompute_persona_status(record: PersonaRecord) -> PersonaRecord:
    if record.voice.status == AssetStatus.FAILED or record.face.status == AssetStatus.FAILED:
        record.status = PersonaStatus.FAILED
    elif record.voice.status == AssetStatus.READY and record.face.status == AssetStatus.READY:
        record.status = PersonaStatus.READY
    else:
        record.status = PersonaStatus.DRAFT
    return record
