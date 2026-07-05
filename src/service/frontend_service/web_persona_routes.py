from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from service.frontend_service.avatar_image_upload import (
    AvatarImageUploadError,
    save_avatar_image_bytes,
)
from service.frontend_service.voice_clone_upload import (
    VoiceCloneUploadError,
    create_cosyvoice_voice_clone,
    find_voice_clone_audio_file,
    is_voice_enrollment_download_error,
    save_voice_clone_audio_bytes,
)
from service.v1_adapter.personas.repository import PersonaRepositoryError
from service.v1_adapter.personas.schemas import PersonaUpsertRequest
from service.v1_adapter.personas.service import PersonaService


WEB_PERSONA_LIST_ROUTE = "/openavatarchat/personas"
WEB_PERSONA_CREATE_ROUTE = "/openavatarchat/personas"
WEB_PERSONA_DETAIL_ROUTE = "/openavatarchat/personas/{persona_id}"
WEB_PERSONA_FACE_UPLOAD_ROUTE = "/openavatarchat/personas/{persona_id}/face"
WEB_PERSONA_VOICE_UPLOAD_ROUTE = "/openavatarchat/personas/{persona_id}/voice"
WEB_PERSONA_FACE_RESET_ROUTE = "/openavatarchat/personas/{persona_id}/face:reset"
WEB_PERSONA_VOICE_RESET_ROUTE = "/openavatarchat/personas/{persona_id}/voice:reset"
WEB_PERSONA_VOICE_AUDIO_ROUTE = "/openavatarchat/personas/voice-clone/audio/{filename}"
WEB_PERSONA_DEVICE_SN = "web_frontend"
WEB_PERSONA_SAMPLE_TEXT = "今天天气真不错，我想去成都喝茶聊天，慢慢说话。"


class WebPersonaUpsertRequest(BaseModel):
    persona_id: Optional[str] = None
    elder_id: Optional[str] = None
    tenant_id: Optional[str] = None
    relationship: Optional[str] = None
    display_name: str
    address_to_elder: Optional[str] = None
    self_reference: Optional[str] = None
    gender: Optional[str] = None
    persona_prompt: Optional[str] = None
    is_default: Optional[bool] = None


def web_persona_frontend_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "list_route": WEB_PERSONA_LIST_ROUTE,
        "create_route": WEB_PERSONA_CREATE_ROUTE,
        "detail_route_template": WEB_PERSONA_DETAIL_ROUTE,
        "face_upload_route_template": WEB_PERSONA_FACE_UPLOAD_ROUTE,
        "voice_upload_route_template": WEB_PERSONA_VOICE_UPLOAD_ROUTE,
        "face_reset_route_template": WEB_PERSONA_FACE_RESET_ROUTE,
        "voice_reset_route_template": WEB_PERSONA_VOICE_RESET_ROUTE,
        "device_sn": os.getenv("WEB_PERSONA_DEVICE_SN", WEB_PERSONA_DEVICE_SN).strip()
        or WEB_PERSONA_DEVICE_SN,
        "sample_text": os.getenv("WEB_PERSONA_SAMPLE_TEXT", WEB_PERSONA_SAMPLE_TEXT).strip()
        or WEB_PERSONA_SAMPLE_TEXT,
    }


def register_web_persona_routes(app: FastAPI, service: Optional[PersonaService] = None) -> None:
    if getattr(app.state, "web_persona_routes_registered", False):
        return
    app.state.web_persona_routes_registered = True
    persona_service = service or PersonaService()

    @app.get(WEB_PERSONA_LIST_ROUTE)
    async def list_web_personas(elder_id: Optional[str] = None, tenant_id: Optional[str] = None):
        try:
            records = persona_service.repository.load_all()
        except PersonaRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        items = list(records.values())
        if elder_id:
            items = [item for item in items if item.elder_id == elder_id]
        if tenant_id:
            items = [item for item in items if item.tenant_id == tenant_id]
        items = sorted(items, key=lambda item: (not item.is_default, item.created_at, item.persona_id))
        default_persona_id = _default_persona_id(items)
        return {
            "status": "ok",
            "default_persona_id": default_persona_id,
            "selected_persona_id": default_persona_id or (items[0].persona_id if items else None),
            "device_sn": web_persona_frontend_config()["device_sn"],
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.post(WEB_PERSONA_CREATE_ROUTE)
    async def create_web_persona(payload: WebPersonaUpsertRequest):
        records = persona_service.repository.load_all()
        owner = _default_owner(records)
        elder_id = _clean(payload.elder_id) or owner["elder_id"]
        tenant_id = _clean(payload.tenant_id) or owner["tenant_id"]
        persona_id = _clean(payload.persona_id) or _new_persona_id(tenant_id, elder_id)
        is_default = payload.is_default if payload.is_default is not None else not bool(records)
        result = persona_service.upsert_persona(
            persona_id,
            PersonaUpsertRequest(
                elder_id=elder_id,
                tenant_id=tenant_id,
                relationship=payload.relationship,
                display_name=payload.display_name,
                address_to_elder=payload.address_to_elder,
                self_reference=payload.self_reference,
                gender=payload.gender,
                persona_prompt=payload.persona_prompt,
                is_default=is_default,
            ),
        )
        return {"status": "ok", **result, "persona": persona_service.get_persona(persona_id)}

    @app.get(WEB_PERSONA_DETAIL_ROUTE)
    async def get_web_persona(persona_id: str):
        return {"status": "ok", "persona": persona_service.get_persona(persona_id)}

    @app.post(WEB_PERSONA_FACE_UPLOAD_ROUTE)
    async def upload_web_persona_face(persona_id: str, file: UploadFile = File(...)):
        content = await file.read()
        try:
            upload_result = save_avatar_image_bytes(
                data=content,
                original_filename=file.filename,
                content_type=file.content_type,
            )
            result = persona_service.upload_face_bytes(
                persona_id=persona_id,
                filename=upload_result.filename,
                content=Path(upload_result.absolute_path).read_bytes(),
            )
        except AvatarImageUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return {"status": "ok", **result, "persona": persona_service.get_persona(persona_id)}

    @app.get(WEB_PERSONA_VOICE_AUDIO_ROUTE)
    async def get_web_persona_voice_audio(filename: str):
        try:
            audio_path = find_voice_clone_audio_file(filename)
        except VoiceCloneUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return FileResponse(audio_path, media_type="audio/wav", filename=filename)

    @app.post(WEB_PERSONA_VOICE_UPLOAD_ROUTE)
    async def upload_web_persona_voice(
        request: Request,
        persona_id: str,
        file: UploadFile = File(...),
        ref_text: str = Form(""),
        source_duration_ms: Optional[str] = Form(None),
    ):
        ref_text = _clean(ref_text) or web_persona_frontend_config()["sample_text"]
        source_duration_value = _optional_int(source_duration_ms)
        data = await file.read()
        try:
            upload_result = save_voice_clone_audio_bytes(
                data=data,
                original_filename=file.filename,
                content_type=file.content_type,
            )
            audio_url = _external_url(
                request,
                _fill_voice_audio_route(upload_result.filename),
            )
            target_model = _voice_clone_target_model()
            voice_id = await asyncio.to_thread(
                create_cosyvoice_voice_clone,
                audio_url=audio_url,
                target_model=target_model,
                prefix=_voice_clone_prefix(persona_id),
                api_key=os.getenv("DASHSCOPE_API_KEY"),
            )
            result = persona_service.upload_voice_bytes(
                persona_id=persona_id,
                filename=upload_result.filename,
                content=Path(upload_result.absolute_path).read_bytes(),
                ref_text=ref_text,
                source_duration_ms=source_duration_value
                if source_duration_value is not None
                else int(upload_result.duration_seconds * 1000),
                voice_id=voice_id,
                model_name=target_model,
                clone_source_url=audio_url,
            )
        except VoiceCloneUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except Exception as exc:
            if is_voice_enrollment_download_error(exc):
                raise HTTPException(
                    status_code=502,
                    detail="Bailian could not download the recorded audio.",
                ) from exc
            raise HTTPException(status_code=500, detail=f"Failed to clone persona voice: {exc}") from exc
        return {
            "status": "ok",
            **result,
            "voice_id": voice_id,
            "model_name": target_model,
            "persona": persona_service.get_persona(persona_id),
        }

    @app.post(WEB_PERSONA_VOICE_RESET_ROUTE)
    async def reset_web_persona_voice(persona_id: str):
        result = persona_service.reset_voice(persona_id)
        return {"status": "ok", **result, "persona": persona_service.get_persona(persona_id)}

    @app.post(WEB_PERSONA_FACE_RESET_ROUTE)
    async def reset_web_persona_face(persona_id: str):
        result = persona_service.reset_face(persona_id)
        return {"status": "ok", **result, "persona": persona_service.get_persona(persona_id)}


def _default_persona_id(items) -> Optional[str]:
    defaults = [item for item in items if item.is_default]
    if defaults:
        return sorted(defaults, key=lambda item: (item.updated_at, item.persona_id), reverse=True)[0].persona_id
    return None


def _default_owner(records) -> Dict[str, str]:
    defaults = [record for record in records.values() if record.is_default]
    source = sorted(defaults or list(records.values()), key=lambda item: (item.updated_at, item.persona_id), reverse=True)
    if source:
        return {"elder_id": source[0].elder_id, "tenant_id": source[0].tenant_id}
    return {
        "elder_id": os.getenv("WEB_PERSONA_ELDER_ID", "web_elder").strip() or "web_elder",
        "tenant_id": os.getenv("WEB_PERSONA_TENANT_ID", "web_tenant").strip() or "web_tenant",
    }


def _new_persona_id(tenant_id: str, elder_id: str) -> str:
    return f"web_{_safe_id(tenant_id)}_{_safe_id(elder_id)}_{uuid.uuid4().hex[:10]}"


def _safe_id(value: str) -> str:
    safe = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in value.strip())[:32].strip("_")
    return safe or "persona"


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="source_duration_ms must be an integer") from exc


def _voice_clone_target_model() -> str:
    return os.getenv("V1_PERSONA_VOICE_TARGET_MODEL", "cosyvoice-v3-flash").strip() or "cosyvoice-v3-flash"


def _voice_clone_prefix(persona_id: str) -> str:
    digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:8]
    return f"p{digest}"


def _fill_voice_audio_route(filename: str) -> str:
    return WEB_PERSONA_VOICE_AUDIO_ROUTE.replace("{filename}", filename)


def _external_url(request: Request, path: str) -> str:
    configured_public_url = os.getenv("OPENAVATARCHAT_PUBLIC_URL", "").strip().rstrip("/")
    if configured_public_url:
        return f"{configured_public_url}{path}"

    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    scheme = (forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme) or "http"
    host = (forwarded_host.split(",")[0].strip() if forwarded_host else request.headers.get("host")) or request.url.netloc
    origin = request.headers.get("origin", "").strip()
    if origin:
        parts = urlsplit(origin)
        if parts.scheme and parts.netloc:
            scheme = parts.scheme
            host = parts.netloc
    if scheme == "http" and host.endswith(":8443"):
        scheme = "https"
    return f"{scheme}://{host}{path}"
