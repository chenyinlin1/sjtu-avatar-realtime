from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, Request

from ..auth import require_secret_key
from ..responses import v1_success
from .schemas import ASRTranscribeRequest
from .service import ASRService


_service = ASRService()


def register_asr_routes(app: FastAPI, service: ASRService = _service) -> None:
    @app.post("/api/v1/asr/transcribe")
    async def transcribe_asr(
        request: Request,
        payload: ASRTranscribeRequest,
        _auth: None = Depends(require_secret_key),
    ):
        data = await asyncio.to_thread(service.transcribe, payload, request.app.state)
        return v1_success(data, request)
