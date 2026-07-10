from __future__ import annotations

import hashlib
import os
from typing import Dict, Optional
from ..responses import V1HTTPException
from .media_storage import MAX_FACE_BYTES, MAX_VOICE_BYTES, MediaStorageError, PersonaMediaStorage
from .models import (
    AssetStatus,
    FaceAsset,
    PersonaRecord,
    VoiceAsset,
    create_persona_record,
    now_ms,
    recompute_persona_status,
)
from .repository import PersonaRepository, PersonaRepositoryError
from .schemas import PersonaUpsertRequest


class PersonaService:
    def __init__(
        self,
        repository: Optional[PersonaRepository] = None,
        media_storage: Optional[PersonaMediaStorage] = None,
    ):
        self.repository = repository or PersonaRepository()
        self.media_storage = media_storage or PersonaMediaStorage()

    def upsert_persona(self, persona_id: str, payload: PersonaUpsertRequest) -> Dict:
        records = self._load_records()
        existing = records.get(persona_id)
        timestamp = now_ms()
        if existing is None:
            record = create_persona_record(
                persona_id=persona_id,
                elder_id=payload.elder_id,
                tenant_id=payload.tenant_id,
                display_name=payload.display_name,
                is_default=payload.is_default,
                relationship=payload.relationship,
                address_to_elder=payload.address_to_elder,
                self_reference=payload.self_reference,
                gender=payload.gender,
                persona_prompt=payload.persona_prompt,
            )
        else:
            if existing.elder_id != payload.elder_id or existing.tenant_id != payload.tenant_id:
                raise V1HTTPException(
                    code="INVALID_PARAM",
                    message="elder_id and tenant_id cannot be changed",
                    status_code=400,
                )
            record = existing
            record.display_name = payload.display_name
            record.relationship = payload.relationship
            record.address_to_elder = payload.address_to_elder
            record.self_reference = payload.self_reference
            record.gender = payload.gender
            record.persona_prompt = payload.persona_prompt
            record.is_default = payload.is_default
            record.updated_at = timestamp

        if payload.is_default:
            self._clear_other_defaults(records, record, timestamp)
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)
        return {"persona_id": record.persona_id, "status": record.status.value}

    def get_persona(self, persona_id: str) -> Dict:
        return self._require_persona(persona_id).model_dump(mode="json")

    def list_personas(self, elder_id: str, tenant_id: str) -> Dict:
        try:
            items = self.repository.list_by_owner(elder_id=elder_id, tenant_id=tenant_id)
        except PersonaRepositoryError as exc:
            raise V1HTTPException("INTERNAL_ERROR", str(exc), 500) from exc
        default_persona_id = next((item.persona_id for item in items if item.is_default), None)
        return {
            "elder_id": elder_id,
            "tenant_id": tenant_id,
            "default_persona_id": default_persona_id,
            "items": [item.model_dump(mode="json") for item in items],
        }

    def delete_persona(self, persona_id: str) -> Dict:
        records = self._load_records()
        records.pop(persona_id, None)
        self._write_records(records)
        self._delete_persona_media(persona_id)
        return {"persona_id": persona_id, "deleted": True}

    def upload_voice_bytes(
        self,
        persona_id: str,
        filename: str,
        content: bytes,
        ref_text: str,
        source_duration_ms: Optional[int] = None,
        voice_id: Optional[str] = None,
        model_name: Optional[str] = None,
        clone_source_url: Optional[str] = None,
    ) -> Dict:
        if not ref_text:
            raise V1HTTPException("INVALID_PARAM", "ref_text is required", 400)
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        try:
            stored = self.media_storage.save_voice_bytes(persona_id, filename, content)
        except MediaStorageError as exc:
            self._mark_voice_failed(
                persona_id,
                fail_reason=exc.message,
                ref_text=ref_text,
                sample_duration_ms=source_duration_ms,
                voice_id=voice_id,
                model_name=model_name,
                clone_source_url=clone_source_url,
            )
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        timestamp = now_ms()
        voice_status = AssetStatus.READY if voice_id else AssetStatus.PROCESSING
        record.voice = VoiceAsset(
            status=voice_status,
            ref_text=ref_text,
            sample_duration_ms=source_duration_ms if source_duration_ms is not None else stored.duration_ms,
            voice_id=voice_id,
            model_name=model_name,
            sample_path=stored.stored_path,
            clone_source_url=clone_source_url,
            fail_reason=None if voice_id else "voice_id is not created; upload by audio_url for runtime voice clone",
            updated_at=timestamp,
        )
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)
        return {"persona_id": persona_id, "voice_status": record.voice.status.value}

    def upload_voice_url(
        self,
        persona_id: str,
        audio_url: str,
        ref_text: str,
        source_duration_ms: Optional[int],
    ) -> Dict:
        self._require_persona(persona_id)
        try:
            content, filename = self.media_storage.download(audio_url, MAX_VOICE_BYTES)
        except MediaStorageError as exc:
            self._mark_voice_failed(
                persona_id,
                fail_reason=exc.message,
                ref_text=ref_text,
                sample_duration_ms=source_duration_ms,
                clone_source_url=audio_url,
            )
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        target_model = self._voice_clone_target_model()
        try:
            voice_id = self._create_voice_clone(
                persona_id=persona_id,
                audio_url=audio_url,
                target_model=target_model,
            )
        except V1HTTPException as exc:
            self._mark_voice_failed(
                persona_id,
                fail_reason=exc.message,
                ref_text=ref_text,
                sample_duration_ms=source_duration_ms,
                model_name=target_model,
                clone_source_url=audio_url,
            )
            raise
        return self.upload_voice_bytes(
            persona_id,
            filename,
            content,
            ref_text,
            source_duration_ms,
            voice_id=voice_id,
            model_name=target_model,
            clone_source_url=audio_url,
        )

    def upload_face_bytes(self, persona_id: str, filename: str, content: bytes) -> Dict:
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        try:
            stored = self.media_storage.save_face_bytes(persona_id, filename, content)
        except MediaStorageError as exc:
            self._mark_face_failed(persona_id, fail_reason=exc.message)
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        timestamp = now_ms()
        record.face = FaceAsset(
            status=AssetStatus.READY,
            image_path=stored.stored_path,
            updated_at=timestamp,
        )
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)
        return {"persona_id": persona_id, "face_status": record.face.status.value}

    def upload_face_url(self, persona_id: str, image_url: str) -> Dict:
        self._require_persona(persona_id)
        try:
            content, filename = self.media_storage.download(image_url, MAX_FACE_BYTES)
        except MediaStorageError as exc:
            self._mark_face_failed(persona_id, fail_reason=exc.message)
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        return self.upload_face_bytes(persona_id, filename, content)

    def reset_voice(self, persona_id: str) -> Dict:
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        try:
            self.media_storage.delete_voice(persona_id)
        except MediaStorageError as exc:
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        timestamp = now_ms()
        record.voice = VoiceAsset(updated_at=timestamp)
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)
        return {"persona_id": persona_id, "voice_status": record.voice.status.value}

    def reset_face(self, persona_id: str) -> Dict:
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        try:
            self.media_storage.delete_face(persona_id)
        except MediaStorageError as exc:
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc
        timestamp = now_ms()
        record.face = FaceAsset(updated_at=timestamp)
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)
        return {"persona_id": persona_id, "face_status": record.face.status.value}

    def _load_records(self) -> Dict[str, PersonaRecord]:
        try:
            return self.repository.load_all()
        except PersonaRepositoryError as exc:
            raise V1HTTPException("INTERNAL_ERROR", str(exc), 500) from exc

    def _write_records(self, records: Dict[str, PersonaRecord]) -> None:
        try:
            self.repository.write_all(records)
        except PersonaRepositoryError as exc:
            raise V1HTTPException("INTERNAL_ERROR", str(exc), 500) from exc

    def _require_persona(self, persona_id: str) -> PersonaRecord:
        try:
            record = self.repository.get(persona_id)
        except PersonaRepositoryError as exc:
            raise V1HTTPException("INTERNAL_ERROR", str(exc), 500) from exc
        if record is None:
            raise V1HTTPException("PERSONA_NOT_FOUND", "persona not found", 404)
        return record

    @staticmethod
    def _require_persona_from_records(records: Dict[str, PersonaRecord], persona_id: str) -> PersonaRecord:
        record = records.get(persona_id)
        if record is None:
            raise V1HTTPException("PERSONA_NOT_FOUND", "persona not found", 404)
        return record

    @staticmethod
    def _clear_other_defaults(records: Dict[str, PersonaRecord], record: PersonaRecord, timestamp: int) -> None:
        for other in records.values():
            if (
                other.persona_id != record.persona_id
                and other.elder_id == record.elder_id
                and other.tenant_id == record.tenant_id
                and other.is_default
            ):
                other.is_default = False
                other.updated_at = timestamp

    def _delete_persona_media(self, persona_id: str) -> None:
        try:
            self.media_storage.delete_persona_media(persona_id)
        except MediaStorageError as exc:
            raise V1HTTPException(exc.code, exc.message, exc.status_code) from exc

    def _mark_voice_failed(
        self,
        persona_id: str,
        *,
        fail_reason: str,
        ref_text: Optional[str] = None,
        sample_duration_ms: Optional[int] = None,
        voice_id: Optional[str] = None,
        model_name: Optional[str] = None,
        clone_source_url: Optional[str] = None,
    ) -> None:
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        timestamp = now_ms()
        record.voice = VoiceAsset(
            status=AssetStatus.FAILED,
            ref_text=ref_text,
            sample_duration_ms=sample_duration_ms,
            voice_id=voice_id,
            model_name=model_name,
            clone_source_url=clone_source_url,
            fail_reason=fail_reason,
            updated_at=timestamp,
        )
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)

    def _mark_face_failed(self, persona_id: str, *, fail_reason: str) -> None:
        records = self._load_records()
        record = self._require_persona_from_records(records, persona_id)
        timestamp = now_ms()
        record.face = FaceAsset(
            status=AssetStatus.FAILED,
            fail_reason=fail_reason,
            updated_at=timestamp,
        )
        record.updated_at = timestamp
        records[persona_id] = recompute_persona_status(record)
        self._write_records(records)

    @staticmethod
    def _voice_clone_target_model() -> str:
        return os.getenv("V1_PERSONA_VOICE_TARGET_MODEL", "cosyvoice-v3-flash").strip() or "cosyvoice-v3-flash"

    @staticmethod
    def _voice_clone_prefix(persona_id: str) -> str:
        digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:8]
        return f"p{digest}"

    def _create_voice_clone(self, *, persona_id: str, audio_url: str, target_model: str) -> str:
        from service.frontend_service.voice_clone_upload import (
            create_cosyvoice_voice_clone,
            is_voice_enrollment_download_error,
        )

        try:
            return create_cosyvoice_voice_clone(
                audio_url=audio_url,
                target_model=target_model,
                prefix=self._voice_clone_prefix(persona_id),
                api_key=os.getenv("DASHSCOPE_API_KEY"),
            )
        except Exception as exc:
            code = "UPSTREAM_TIMEOUT" if is_voice_enrollment_download_error(exc) else "INTERNAL_ERROR"
            raise V1HTTPException(code, f"failed to create voice clone: {exc}", 502) from exc
