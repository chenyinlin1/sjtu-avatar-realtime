from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from chat_engine.chat_engine import OPEN_AVATAR_CHAT_VERSION

from .personas.routes import register_persona_routes
from .responses import (
    V1HTTPException,
    v1_fastapi_http_exception_handler,
    v1_http_exception_handler,
    v1_request_validation_exception_handler,
    v1_success,
)


def register_v1_adapter(app: FastAPI) -> None:
    if getattr(app.state, "v1_adapter_registered", False):
        return
    app.state.v1_adapter_registered = True

    app.add_exception_handler(V1HTTPException, v1_http_exception_handler)
    app.add_exception_handler(RequestValidationError, v1_request_validation_exception_handler)
    app.add_exception_handler(HTTPException, v1_fastapi_http_exception_handler)

    @app.get("/api/v1/health")
    async def v1_health(request: Request):
        return v1_success({"status": "UP"}, request)

    @app.get("/api/v1/version")
    async def v1_version(request: Request):
        return v1_success(
            {
                "version": OPEN_AVATAR_CHAT_VERSION,
                "api_version": "v1",
                "avatar_engine": "openavatarchat",
            },
            request,
        )

    register_persona_routes(app)
