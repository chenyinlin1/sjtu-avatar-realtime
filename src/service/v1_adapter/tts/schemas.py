from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    persona_id: Optional[str] = None
    elder_id: Optional[str] = None
    tenant_id: Optional[str] = None

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text is required")
        return cleaned

    @field_validator("persona_id", "elder_id", "tenant_id", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None
