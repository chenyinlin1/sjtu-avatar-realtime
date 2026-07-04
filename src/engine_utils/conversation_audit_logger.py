from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, Optional

from loguru import logger

from engine_utils.directory_info import DirectoryInfo


_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_MAX_TEXT_CHARS = 12000
_DEFAULT_QUEUE_SIZE = 4096
_MAX_STREAM_TURN_KEYS = 20000

_event_queue: Optional[queue.Queue] = None
_worker_started = False
_worker_lock = threading.Lock()

_turn_lock = threading.Lock()
_turn_counters: DefaultDict[str, int] = defaultdict(int)
_stream_turns: Dict[str, str] = {}
_sequence = 0


def audit_event(
    context: Any,
    event: str,
    *,
    stream_identity: Any = None,
    stream_key: Optional[str] = None,
    source_stream_key: Optional[str] = None,
    bind_stream_key: Optional[str] = None,
    turn_id: Optional[str] = None,
    create_turn: bool = False,
    **payload: Any,
) -> Optional[str]:
    """Record an audit event without affecting the realtime conversation flow."""
    if not _enabled():
        return turn_id
    try:
        session_id = _session_id(context)
        stream_key = stream_key or _stream_key(stream_identity)
        resolved_turn_id = _resolve_turn_id(
            context,
            session_id=session_id,
            stream_identity=stream_identity,
            stream_key=stream_key,
            source_stream_key=source_stream_key,
            explicit_turn_id=turn_id,
            create_turn=create_turn,
        )
        if bind_stream_key and resolved_turn_id:
            bind_stream(session_id, bind_stream_key, resolved_turn_id)

        record = {
            "schema": "openavatarchat.conversation_audit.v1",
            "sequence": _next_sequence(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "unix_ms": int(time.time() * 1000),
            "event": event,
            "session_id": session_id,
            "turn_id": resolved_turn_id,
            "stream": {
                "current": stream_key,
                "source": source_stream_key,
                "bind": bind_stream_key,
                "data_type": _stream_data_type(stream_identity),
            },
            "identity": _identity_snapshot(context),
            "payload": _sanitize(payload),
        }
        _enqueue(record)
        return resolved_turn_id
    except Exception as exc:
        logger.warning(f"Conversation audit event failed: {exc}")
        return turn_id


def bind_stream(session_id: Optional[str], stream_key: Optional[str], turn_id: Optional[str]) -> None:
    if not session_id or not stream_key or not turn_id:
        return
    with _turn_lock:
        if len(_stream_turns) >= _MAX_STREAM_TURN_KEYS:
            _stream_turns.pop(next(iter(_stream_turns)), None)
        _stream_turns[_turn_key(session_id, stream_key)] = turn_id


def flush_audit_events(timeout: float = 1.0) -> None:
    """Best-effort flush helper used by tests and shutdown diagnostics."""
    q = _event_queue
    if q is None:
        return
    done = threading.Event()

    def _wait_for_queue():
        q.join()
        done.set()

    thread = threading.Thread(target=_wait_for_queue, daemon=True)
    thread.start()
    done.wait(timeout)


def _resolve_turn_id(
    context: Any,
    *,
    session_id: Optional[str],
    stream_identity: Any,
    stream_key: Optional[str],
    source_stream_key: Optional[str],
    explicit_turn_id: Optional[str],
    create_turn: bool,
) -> Optional[str]:
    if explicit_turn_id:
        for key in (stream_key, source_stream_key):
            bind_stream(session_id, key, explicit_turn_id)
        return explicit_turn_id
    if not session_id:
        return None

    with _turn_lock:
        for key in (stream_key, source_stream_key):
            found = _stream_turns.get(_turn_key(session_id, key))
            if found:
                return found

    found = _turn_from_ancestry(context, session_id, stream_identity)
    if found:
        bind_stream(session_id, stream_key, found)
        return found

    if not create_turn:
        return None

    with _turn_lock:
        _turn_counters[session_id] += 1
        new_turn = f"{session_id}:{_turn_counters[session_id]:06d}"
        for key in (stream_key, source_stream_key):
            if key:
                _stream_turns[_turn_key(session_id, key)] = new_turn
        return new_turn


def _turn_from_ancestry(context: Any, session_id: str, stream_identity: Any) -> Optional[str]:
    stream_manager = getattr(context, "stream_manager", None)
    if stream_manager is None or stream_identity is None:
        return None
    try:
        ancestry = stream_manager.get_stream_ancestry(stream_identity)
    except Exception:
        return None
    related = []
    for key in ("parents", "ancestors"):
        values = ancestry.get(key) if isinstance(ancestry, dict) else None
        if isinstance(values, list):
            related.extend(values)
    with _turn_lock:
        for identity in related:
            found = _stream_turns.get(_turn_key(session_id, _stream_key(identity)))
            if found:
                return found
    return None


def _enqueue(record: Dict[str, Any]) -> None:
    try:
        q = _ensure_worker()
        q.put_nowait(record)
    except queue.Full:
        logger.warning("Conversation audit queue full; event dropped")
    except Exception as exc:
        logger.warning(f"Conversation audit enqueue failed: {exc}")


def _ensure_worker() -> queue.Queue:
    global _event_queue, _worker_started
    if _event_queue is not None and _worker_started:
        return _event_queue
    with _worker_lock:
        if _event_queue is None:
            _event_queue = queue.Queue(
                maxsize=_env_int("OPENAVATAR_CONVERSATION_AUDIT_QUEUE_SIZE", _DEFAULT_QUEUE_SIZE)
            )
        if not _worker_started:
            thread = threading.Thread(target=_writer_loop, name="conversation-audit-writer", daemon=True)
            thread.start()
            _worker_started = True
    return _event_queue


def _writer_loop() -> None:
    assert _event_queue is not None
    while True:
        record = _event_queue.get()
        try:
            _write_record(record)
        except Exception as exc:
            logger.warning(f"Conversation audit write failed: {exc}")
        finally:
            _event_queue.task_done()


def _write_record(record: Dict[str, Any]) -> None:
    log_dir = _log_dir()
    os.makedirs(log_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = os.path.join(log_dir, f"conversation_audit_{day}.jsonl")
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _identity_snapshot(context: Any) -> Dict[str, Any]:
    shared_states = getattr(context, "shared_states", None)
    device_info = _as_dict(getattr(shared_states, "device_info", None))
    if not device_info:
        device_info = _as_dict(getattr(context, "device_info", None))
    persona_runtime = _as_dict(getattr(shared_states, "persona_runtime", None))
    endpoint = getattr(shared_states, "client_endpoint", None)

    elder_id = device_info.get("elder_id") or persona_runtime.get("elder_id")
    tenant_id = device_info.get("tenant_id") or persona_runtime.get("tenant_id")
    persona_id = persona_runtime.get("persona_id") or device_info.get("persona_id")
    role_info = {
        "relationship": persona_runtime.get("relationship"),
        "display_name": persona_runtime.get("display_name"),
        "address_to_elder": persona_runtime.get("address_to_elder"),
        "self_reference": persona_runtime.get("self_reference"),
        "gender": persona_runtime.get("gender"),
        "persona_prompt": persona_runtime.get("persona_prompt"),
        "persona_system_prompt": persona_runtime.get("persona_system_prompt"),
        "voice_ready": persona_runtime.get("voice_ready"),
        "voice_id": persona_runtime.get("voice_id"),
        "voice_model_name": persona_runtime.get("voice_model_name"),
        "face_ready": persona_runtime.get("face_ready"),
        "face_image_path": persona_runtime.get("face_image_path"),
    }
    return _sanitize({
        "device_sn": device_info.get("device_sn"),
        "person_id": device_info.get("person_id") or elder_id,
        "elder_id": elder_id,
        "tenant_id": tenant_id,
        "client_endpoint": endpoint,
        "is_speaker_endpoint_guess": endpoint in {"ws", "ws_voice", "speaker"} or bool(device_info.get("device_sn")),
        "role_id": persona_id,
        "persona_id": persona_id,
        "role_info": role_info,
    })


def _sanitize(value: Any) -> Any:
    max_chars = _env_int("OPENAVATAR_CONVERSATION_AUDIT_MAX_TEXT_CHARS", _DEFAULT_MAX_TEXT_CHARS)
    if isinstance(value, str):
        if len(value) > max_chars:
            return value[:max_chars] + f"...<truncated {len(value) - max_chars} chars>"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _sanitize(value.model_dump())
        except Exception:
            pass
    try:
        return _sanitize(vars(value))
    except Exception:
        return repr(value)


def _session_id(context: Any) -> Optional[str]:
    value = getattr(context, "session_id", None)
    if value:
        return str(value)
    session_info = getattr(context, "session_info", None)
    value = getattr(session_info, "session_id", None)
    return str(value) if value else None


def _stream_key(stream_identity: Any) -> Optional[str]:
    if stream_identity is None:
        return None
    value = getattr(stream_identity, "stream_key_str", None)
    if value:
        return str(value)
    key = getattr(stream_identity, "key", None)
    return str(key) if key else None


def _stream_data_type(stream_identity: Any) -> Optional[str]:
    data_type = getattr(stream_identity, "data_type", None)
    value = getattr(data_type, "value", None)
    return str(value) if value else None


def _turn_key(session_id: str, stream_key: Optional[str]) -> str:
    return f"{session_id}:{stream_key}"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _enabled() -> bool:
    value = os.getenv("OPENAVATAR_CONVERSATION_AUDIT_ENABLED", "1").strip().lower()
    return value not in _FALSE_VALUES


def _log_dir() -> str:
    return os.getenv("OPENAVATAR_CONVERSATION_AUDIT_LOG_DIR") or os.path.join(DirectoryInfo.get_project_dir(), "logs")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _next_sequence() -> int:
    global _sequence
    with _turn_lock:
        _sequence += 1
        return _sequence
