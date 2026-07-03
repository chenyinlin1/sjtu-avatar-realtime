from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersonaUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    elder_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    is_default: bool = False

    @field_validator("elder_id", "tenant_id", "display_name")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be empty")
        return stripped


class VoiceUrlUploadRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    audio_url: str = Field(min_length=1)
    ref_text: str = Field(min_length=1)
    source_duration_ms: Optional[int] = None

    @field_validator("audio_url", "ref_text")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be empty")
        return stripped


class FaceUrlUploadRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image_url: str = Field(min_length=1)

    @field_validator("image_url")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be empty")
        return stripped
