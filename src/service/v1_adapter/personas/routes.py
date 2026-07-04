from __future__ import annotations

from typing import Any, Optional, Type

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..auth import require_secret_key
from ..responses import V1HTTPException, v1_success
from .schemas import FaceUrlUploadRequest, PersonaUpsertRequest, VoiceUrlUploadRequest
from .service import PersonaService


_service = PersonaService()


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    return errors[0].get("msg", "invalid request") if errors else "invalid request"


def _validate_payload(model: Type[BaseModel], payload: Any):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise V1HTTPException("INVALID_PARAM", _validation_message(exc), 400) from exc


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise V1HTTPException("INVALID_PARAM", "invalid json body", 400) from exc


def _parse_optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise V1HTTPException("INVALID_PARAM", "source_duration_ms must be an integer", 400) from exc


def _require_upload_file(value: Any, field_name: str = "file") -> StarletteUploadFile:
    if not isinstance(value, StarletteUploadFile):
        raise V1HTTPException("INVALID_PARAM", f"{field_name} is required", 400)
    return value


def register_persona_routes(app: FastAPI, service: PersonaService = _service) -> None:
    @app.get("/api/v1/personas")
    async def list_personas(
        request: Request,
        elder_id: str = Query(..., min_length=1),
        tenant_id: str = Query(..., min_length=1),
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.list_personas(elder_id=elder_id, tenant_id=tenant_id), request)

    @app.put("/api/v1/personas/{persona_id}")
    async def upsert_persona(
        request: Request,
        persona_id: str,
        payload: PersonaUpsertRequest,
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.upsert_persona(persona_id, payload), request)

    @app.get("/api/v1/personas/{persona_id}")
    async def get_persona(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.get_persona(persona_id), request)

    @app.delete("/api/v1/personas/{persona_id}")
    async def delete_persona(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.delete_persona(persona_id), request)

    @app.post("/api/v1/personas/{persona_id}/voice")
    async def upload_voice(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        content_type = request.headers.get("content-type", "").lower()
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = _require_upload_file(form.get("file"))
            ref_text = str(form.get("ref_text") or "").strip()
            source_duration_ms = _parse_optional_int(form.get("source_duration_ms"))
            return v1_success(
                service.upload_voice_bytes(
                    persona_id=persona_id,
                    filename=file.filename or "sample.wav",
                    content=await file.read(),
                    ref_text=ref_text,
                    source_duration_ms=source_duration_ms,
                ),
                request,
            )

        payload = _validate_payload(VoiceUrlUploadRequest, await _json_body(request))
        return v1_success(
            service.upload_voice_url(
                persona_id=persona_id,
                audio_url=payload.audio_url,
                ref_text=payload.ref_text,
                source_duration_ms=payload.source_duration_ms,
            ),
            request,
        )

    @app.post("/api/v1/personas/{persona_id}/face")
    async def upload_face(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        content_type = request.headers.get("content-type", "").lower()
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = _require_upload_file(form.get("file"))
            return v1_success(
                service.upload_face_bytes(
                    persona_id=persona_id,
                    filename=file.filename or "face.jpg",
                    content=await file.read(),
                ),
                request,
            )

        payload = _validate_payload(FaceUrlUploadRequest, await _json_body(request))
        return v1_success(service.upload_face_url(persona_id, payload.image_url), request)

    @app.post("/api/v1/personas/{persona_id}/voice:reset")
    async def reset_voice(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.reset_voice(persona_id), request)

    @app.post("/api/v1/personas/{persona_id}/face:reset")
    async def reset_face(
        request: Request,
        persona_id: str,
        _auth: None = Depends(require_secret_key),
    ):
        return v1_success(service.reset_face(persona_id), request)
