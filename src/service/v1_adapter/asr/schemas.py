from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ASRTranscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_base64: str = Field(..., min_length=1)
    audio_format: Literal["pcm"] = "pcm"
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1

    @field_validator("audio_base64")
    @classmethod
    def normalize_audio_base64(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("audio_base64 is required")
        return cleaned


