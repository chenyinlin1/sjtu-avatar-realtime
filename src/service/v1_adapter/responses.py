from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class V1HTTPException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _request_id(request: Optional[Request]) -> str:
    if request is not None:
        provided = request.headers.get("X-Request-Id") or request.headers.get("X-Request-ID")
        if provided:
            return provided
    return f"req_{uuid4().hex}"


def v1_success(data: Any, request: Optional[Request] = None) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "message": "ok",
            "request_id": _request_id(request),
            "data": data,
        },
    )


def v1_error(code: str, message: str, status_code: int, request: Optional[Request] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "request_id": _request_id(request),
            "data": {},
        },
    )


async def v1_http_exception_handler(request: Request, exc: V1HTTPException) -> JSONResponse:
    return v1_error(exc.code, exc.message, exc.status_code, request)


async def v1_request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1"):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    message = errors[0].get("msg", "invalid request") if errors else "invalid request"
    return v1_error("INVALID_PARAM", message, 400, request)


async def v1_fastapi_http_exception_handler(request: Request, exc: HTTPException):
    if not request.url.path.startswith("/api/v1"):
        return await http_exception_handler(request, exc)
    code = "INVALID_PARAM" if exc.status_code < 500 else "INTERNAL_ERROR"
    if exc.status_code in {401, 403}:
        code = "UNAUTHORIZED" if exc.status_code == 401 else "FORBIDDEN"
    elif exc.status_code == 404:
        code = "PERSONA_NOT_FOUND"
    message = str(exc.detail) if exc.detail else "request failed"
    return v1_error(code, message, exc.status_code, request)
