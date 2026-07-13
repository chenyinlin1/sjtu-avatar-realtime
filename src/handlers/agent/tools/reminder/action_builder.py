"""Builders for the v1 §10 reminder client_action structures."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional


def new_action_id() -> str:
    """Generate a stable idempotency key for one business operation."""
    return f"reminder-{uuid.uuid4()}"


def build_create_action(
    *,
    title: str,
    remind_at_ms: int,
    repeat: str,
    speak_text: Optional[str] = None,
    action_id: Optional[str] = None,
) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "title": title,
        "remind_at": int(remind_at_ms),
        "repeat": repeat,
    }
    if speak_text:
        args["speak_text"] = speak_text
    return {
        "type": "reminder.create",
        "action_id": action_id or new_action_id(),
        "args": args,
    }


def build_cancel_action(
    *, entity_id: int, action_id: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "type": "reminder.cancel",
        "action_id": action_id or new_action_id(),
        "args": {"entity_id": int(entity_id)},
        "confirm": True,
    }
