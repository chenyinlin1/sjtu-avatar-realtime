"""Session-scoped matching of client actions to asynchronous action_ack events."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any, Dict, Optional

from loguru import logger


@dataclass(frozen=True)
class ActionAckResult:
    action_id: str
    status: str
    ok: bool
    entity_id: Optional[int] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


@dataclass
class _PendingAction:
    action_id: str
    action_type: str
    metadata: Dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    event: Event = field(default_factory=Event)
    ack: Optional[ActionAckResult] = None


class PendingActionRegistry:
    """Thread-safe per-session pending registry with bounded duplicate memory."""

    def __init__(self, session_id: str, *, max_completed: int = 256):
        self.session_id = session_id
        self._max_completed = max_completed
        self._lock = RLock()
        self._pending: Dict[str, _PendingAction] = {}
        self._completed: OrderedDict[str, str] = OrderedDict()
        self._closed = False

    def register(
        self,
        action_id: str,
        action_type: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("reminder action registry is closed")
            if action_id in self._completed:
                raise ValueError(f"action_id was already completed: {action_id}")
            existing = self._pending.get(action_id)
            if existing is not None:
                if existing.action_type != action_type:
                    raise ValueError(
                        f"action_id is already registered for {existing.action_type}"
                    )
                return
            self._pending[action_id] = _PendingAction(
                action_id=action_id,
                action_type=action_type,
                metadata=dict(metadata or {}),
            )
        logger.info(
            f"[{self.session_id}] reminder action registered: "
            f"type={action_type} action_id={action_id} stage=tool_called"
        )

    def resolve(self, data: Dict[str, Any]) -> str:
        action_id = str(data.get("action_id") or "").strip()
        if not action_id:
            logger.warning(f"[{self.session_id}] action_ack missing action_id")
            return "invalid"
        with self._lock:
            if self._closed:
                logger.info(
                    f"[{self.session_id}] late action_ack ignored after session close: action_id={action_id}"
                )
                return "closed"
            if action_id in self._completed:
                logger.info(
                    f"[{self.session_id}] duplicate action_ack ignored: action_id={action_id}"
                )
                return "duplicate"
            pending = self._pending.get(action_id)
            if pending is None:
                logger.info(
                    f"[{self.session_id}] unknown action_ack ignored: action_id={action_id}"
                )
                return "unknown"
            if pending.ack is not None:
                logger.info(
                    f"[{self.session_id}] duplicate action_ack ignored: action_id={action_id}"
                )
                return "duplicate"

            ok = data.get("ok") is True or str(data.get("ok")).strip().lower() == "true"
            entity_id = _optional_positive_int(data.get("entity_id"))
            error, error_code = _normalize_error(data.get("error"))
            pending.ack = ActionAckResult(
                action_id=action_id,
                status="succeeded" if ok else "failed",
                ok=ok,
                entity_id=entity_id,
                error=error,
                error_code=error_code,
            )
            pending.event.set()
            action_type = pending.action_type
        logger.info(
            f"[{self.session_id}] action_ack received: type={action_type} "
            f"action_id={action_id} entity_id={entity_id} ok={ok} "
            f"error_code={error_code or '-'} stage=ack_received"
        )
        return "matched"

    def wait(self, action_id: str, timeout: float) -> ActionAckResult:
        with self._lock:
            pending = self._pending.get(action_id)
            if pending is None:
                return ActionAckResult(
                    action_id=action_id,
                    status="unknown",
                    ok=False,
                    error="pending action is not registered",
                    error_code="UNKNOWN_ACTION",
                )
            event = pending.event

        received = event.wait(max(0.0, timeout))
        with self._lock:
            pending = self._pending.pop(action_id, pending)
            ack = pending.ack
            if not received or ack is None:
                ack = ActionAckResult(
                    action_id=action_id,
                    status="timeout",
                    ok=False,
                    error="action_ack timed out",
                    error_code="ACK_TIMEOUT",
                )
            self._remember_completed(action_id, ack.status)
        if ack.status == "timeout":
            logger.warning(
                f"[{self.session_id}] reminder action timeout: type={pending.action_type} "
                f"action_id={action_id} timeout_seconds={timeout} stage=ack_timeout"
            )
        return ack

    def abort(
        self,
        action_id: str,
        *,
        error: str,
        error_code: str,
    ) -> ActionAckResult:
        """Remove an action that could not be handed to the client transport."""
        with self._lock:
            pending = self._pending.pop(action_id, None)
            result = ActionAckResult(
                action_id=action_id,
                status="send_failed",
                ok=False,
                error=error,
                error_code=error_code,
            )
            if pending is not None:
                pending.ack = result
                pending.event.set()
                self._remember_completed(action_id, result.status)
        logger.warning(
            f"[{self.session_id}] reminder action aborted before client delivery: "
            f"action_id={action_id} error_code={error_code} stage=send_failed"
        )
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for pending in self._pending.values():
                if pending.ack is None:
                    pending.ack = ActionAckResult(
                        action_id=pending.action_id,
                        status="session_closed",
                        ok=False,
                        error="session closed before action_ack",
                        error_code="SESSION_CLOSED",
                    )
                pending.event.set()
        logger.info(f"[{self.session_id}] reminder action registry closed")

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def metadata_for(self, action_id: str) -> Dict[str, Any]:
        with self._lock:
            pending = self._pending.get(action_id)
            return dict(pending.metadata) if pending else {}

    def _remember_completed(self, action_id: str, status: str) -> None:
        self._completed[action_id] = status
        self._completed.move_to_end(action_id)
        while len(self._completed) > self._max_completed:
            self._completed.popitem(last=False)


_ATTACH_LOCK = RLock()
_REGISTRY_ATTRIBUTE = "reminder_pending_actions"


def get_pending_action_registry(
    shared_states: Any,
    session_id: str,
    *,
    create: bool = True,
) -> Optional[PendingActionRegistry]:
    """Get the registry stored on this session's shared state object."""
    if shared_states is None:
        return PendingActionRegistry(session_id) if create else None
    registry = getattr(shared_states, _REGISTRY_ATTRIBUTE, None)
    if isinstance(registry, PendingActionRegistry) or not create:
        return registry if isinstance(registry, PendingActionRegistry) else None
    with _ATTACH_LOCK:
        registry = getattr(shared_states, _REGISTRY_ATTRIBUTE, None)
        if not isinstance(registry, PendingActionRegistry):
            registry = PendingActionRegistry(session_id)
            setattr(shared_states, _REGISTRY_ATTRIBUTE, registry)
        return registry


def close_pending_action_registry(shared_states: Any) -> None:
    registry = get_pending_action_registry(shared_states, "unknown", create=False)
    if registry is not None:
        registry.close()


def _optional_positive_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_error(value: Any) -> tuple[Optional[str], Optional[str]]:
    if value is None:
        return None, None
    if isinstance(value, dict):
        code = str(value.get("code") or "").strip() or None
        message = str(value.get("message") or code or "action failed").strip()
        return message[:300], code[:80] if code else None
    message = str(value).strip() or "action failed"
    code = message if re_safe_code(message) else "ACTION_FAILED"
    return message[:300], code[:80]


def re_safe_code(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 80
        and all(char.isalnum() or char in "_-.:" for char in value)
    )
