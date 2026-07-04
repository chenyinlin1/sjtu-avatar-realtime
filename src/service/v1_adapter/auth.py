from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header

from .responses import V1HTTPException


def _configured_key(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _require_matching_key(provided: Optional[str], expected: str, missing_message: str) -> None:
    if not expected:
        raise V1HTTPException("UNAUTHORIZED", missing_message, 401)
    if not provided or not hmac.compare_digest(provided, expected):
        raise V1HTTPException("UNAUTHORIZED", "invalid api key", 401)


async def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")) -> None:
    expected = _configured_key("API_KEY", "V1_API_KEY")
    _require_matching_key(x_api_key, expected, "API_KEY is not configured")


async def require_secret_key(secret_key: Optional[str] = Header(default=None, alias="secretKey")) -> None:
    expected = _configured_key("DEVICE_SECRET_KEY", "DEVICE_KEY", "CHATROBOT_SECRET_KEY")
    _require_matching_key(secret_key, expected, "DEVICE_SECRET_KEY is not configured")


async def require_device_key(
    x_device_key: Optional[str] = Header(default=None, alias="X-Device-Key"),
    secret_key: Optional[str] = Header(default=None, alias="secretKey"),
) -> None:
    expected = _configured_key("DEVICE_SECRET_KEY", "DEVICE_KEY", "CHATROBOT_SECRET_KEY")
    _require_matching_key(x_device_key or secret_key, expected, "DEVICE_SECRET_KEY is not configured")
