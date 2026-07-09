from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, Request

from ..auth import require_secret_key
from ..responses import v1_success
from .schemas import TTSSynthesizeRequest
from .service import TTSService


_service = TTSService()


def register_tts_routes(app: FastAPI, service: TTSService = _service) -> None:
    @app.post("/api/v1/tts/synthesize")
    async def synthesize_tts(
        request: Request,
        payload: TTSSynthesizeRequest,
        _auth: None = Depends(require_secret_key),
    ):
        data = await asyncio.to_thread(service.synthesize, payload, request.app.state)
        return v1_success(data, request)
