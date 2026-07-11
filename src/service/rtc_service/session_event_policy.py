"""Session-level policy for speaker ClientEvent messages.

This module owns event state and business decisions. Semantic resolution is delegated,
while RTC transport remains in ``rtc_stream.py`` and is injected through small callbacks.
"""

import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any, Callable, Deque, Dict, Optional, Set

from loguru import logger
from pydantic import BaseModel, Field, model_validator

from service.rtc_service.session_event_semantics import SessionEventSemanticResolver


_EVENT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="client-event")


class SessionEventPolicyConfig(BaseModel):
    enabled: bool = True
    silence_level1_ms: int = Field(default=60000, ge=1000)
    silence_level2_ms: int = Field(default=90000, ge=1000)
    event_max_age_ms: int = Field(default=120000, ge=1000)
    event_future_tolerance_ms: int = Field(default=30000, ge=0)
    silence_level1_text: str = "您还在听吗？想聊啥子都可以。"
    silence_level2_text: str = "那您先歇一会儿，有需要再喊我。"
    farewell_text: str = "好嘞，您早点休息，有需要再喊我。"
    exit_intent_enabled: bool = True
    exit_intent_api_url: str = "https://api.deepseek.com"
    exit_intent_api_key: Optional[str] = Field(default=None, repr=False)
    exit_intent_model_name: str = "deepseek-v4-flash"
    exit_intent_timeout_seconds: float = Field(default=2.5, gt=0.1, le=10.0)
    exit_intent_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    reminder_capture_enabled: bool = True
    reminder_extract_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    reminder_success_text: str = "好的，提醒已经记好了。"
    reminder_failure_text: str = "这个提醒没记上，您再说一遍时间吧。"

    @model_validator(mode="after")
    def validate_silence_thresholds(self):
        if self.silence_level2_ms <= self.silence_level1_ms:
            raise ValueError("silence_level2_ms must be greater than silence_level1_ms")
        return self


class SessionEventPolicy:
    """Evaluate ClientEvent messages without depending on RTC or ChatEngine types."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]],
        *,
        session_id: Callable[[], Optional[str]],
        send_action: Callable[[Dict[str, Any], Optional[str]], None],
        emit_interrupt: Callable[[str], None],
        runtime_snapshot: Callable[[], Dict[str, Any]],
    ):
        self.config = SessionEventPolicyConfig.model_validate(config or {}).model_dump()
        self._session_id = session_id
        self._send_action = send_action
        self._emit_interrupt = emit_interrupt
        self._runtime_snapshot = runtime_snapshot
        self._lock = RLock()
        self._handled_ids: Deque[str] = deque()
        self._handled_id_set: Set[str] = set()
        self._last_silence_level = 0
        self._session_ending = False
        self._user_activity_generation = 0
        self._exit_intent_inflight = False
        self._reminder_capture_inflight = False
        self._pending_reminder_actions: Dict[str, str] = {}
        self._semantics = SessionEventSemanticResolver(
            self.config,
            runtime_snapshot=self._runtime_snapshot,
            session_id=self._session_id,
        )

    @property
    def session_ending(self) -> bool:
        with self._lock:
            return self._session_ending

    def note_user_activity(self, source: str = "unknown") -> None:
        with self._lock:
            self._user_activity_generation += 1
            self._last_silence_level = 0
        logger.debug(f"[{self._session_id()}] session policy user activity: {source}")

    def handle_client_event(self, payload: Dict[str, Any], request_id: str) -> None:
        if not self.config["enabled"]:
            return
        event_type = str(payload.get("type") or "").strip().lower()
        if not event_type or not self._event_is_fresh(payload.get("ts")):
            logger.warning(
                f"[{self._session_id()}] ClientEvent rejected: "
                f"type={event_type} ts={payload.get('ts')}"
            )
            return
        if not self._remember_event(request_id):
            logger.info(f"[{self._session_id()}] duplicate ClientEvent ignored: request_id={request_id}")
            return
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        handlers = {
            "user_silence": self._handle_user_silence,
            "user_exit_hint": self._handle_user_exit_hint,
            "reminder_capture": self._handle_reminder_capture,
            "reminder_due": self._handle_reminder_due,
            "reminder_ack": self._handle_reminder_ack,
        }
        if event_type == "wake":
            self.note_user_activity("wake")
        elif event_type == "ui_end":
            self._handle_ui_end(request_id)
        elif event_type == "play_error":
            logger.info(f"[{self._session_id()}] client action play_error received")
        elif event_type in handlers:
            handlers[event_type](data, request_id)
        else:
            logger.debug(f"[{self._session_id()}] unknown ClientEvent ignored: type={event_type}")

    def _remember_event(self, request_id: str) -> bool:
        with self._lock:
            if request_id in self._handled_id_set:
                return False
            while len(self._handled_ids) >= 256:
                expired = self._handled_ids.popleft()
                self._handled_id_set.discard(expired)
            self._handled_ids.append(request_id)
            self._handled_id_set.add(request_id)
            return True

    def _event_is_fresh(self, event_ts: Any) -> bool:
        try:
            ts_ms = int(event_ts)
        except (TypeError, ValueError):
            return False
        now_ms = int(time.time() * 1000)
        return (
            now_ms - int(self.config["event_max_age_ms"])
            <= ts_ms
            <= now_ms + int(self.config["event_future_tolerance_ms"])
        )

    def _runtime_closed(self) -> bool:
        return bool(self._runtime_snapshot().get("closed"))

    def _mark_ending(self) -> bool:
        with self._lock:
            if self._session_ending:
                return False
            self._session_ending = True
            return True

    def _handle_user_silence(self, data: Dict[str, Any], request_id: str) -> None:
        try:
            level = int(data.get("level"))
            silence_ms = int(data.get("silence_ms"))
        except (TypeError, ValueError):
            logger.warning(f"[{self._session_id()}] invalid user_silence payload")
            return
        if level not in (1, 2):
            logger.warning(f"[{self._session_id()}] invalid user_silence level={level}")
            return
        threshold = self.config["silence_level1_ms" if level == 1 else "silence_level2_ms"]
        if silence_ms < int(threshold):
            logger.info(
                f"[{self._session_id()}] user_silence ignored below threshold: "
                f"level={level} silence_ms={silence_ms}"
            )
            return
        runtime = self._runtime_snapshot()
        if runtime.get("music_active"):
            logger.info(f"[{self._session_id()}] user_silence ignored while music is active")
            return
        if runtime.get("avatar_output_active") or runtime.get("closed"):
            logger.info(f"[{self._session_id()}] user_silence ignored while output is active")
            return
        with self._lock:
            if self._session_ending or level <= self._last_silence_level:
                return
            self._last_silence_level = level
            if level == 2:
                self._session_ending = True
        if level == 1:
            self._send_action({
                "type": "say",
                "text": self.config["silence_level1_text"],
                "then": "keep",
                "reason": "user_silence_level1",
            }, request_id)
        else:
            self._send_action({
                "type": "say",
                "text": self.config["silence_level2_text"],
                "then": "end",
                "reason": "idle_timeout",
            }, request_id)

    def _handle_ui_end(self, request_id: str) -> None:
        if not self._mark_ending():
            return
        self._emit_interrupt("ui_end")
        self._send_action({"type": "session.end", "reason": "ui_end"}, request_id)

    def _handle_user_exit_hint(self, data: Dict[str, Any], request_id: str) -> None:
        if not self.config["exit_intent_enabled"]:
            return
        text = str(data.get("text") or "").strip()
        if not text or len(text) > 500:
            logger.warning(f"[{self._session_id()}] invalid user_exit_hint text")
            return
        with self._lock:
            if self._session_ending or self._exit_intent_inflight:
                return
            self._exit_intent_inflight = True
            activity_generation = self._user_activity_generation
        try:
            future = _EVENT_EXECUTOR.submit(self._semantics.classify_exit_intent, text)
        except RuntimeError as exc:
            with self._lock:
                self._exit_intent_inflight = False
            logger.opt(exception=exc).warning(f"[{self._session_id()}] exit intent submit failed")
            return
        future.add_done_callback(
            lambda done: self._finish_user_exit_hint(done, request_id, activity_generation)
        )

    def _finish_user_exit_hint(
        self, future: Future, request_id: str, activity_generation: int
    ) -> None:
        with self._lock:
            self._exit_intent_inflight = False
        try:
            result = future.result()
        except Exception as exc:
            logger.opt(exception=exc).warning(f"[{self._session_id()}] exit intent classification failed")
            return
        if not result:
            return
        is_exit, confidence = result
        threshold = float(self.config["exit_intent_confidence"])
        logger.info(
            f"[{self._session_id()}] exit intent result: is_exit={is_exit} "
            f"confidence={confidence:.2f} threshold={threshold:.2f}"
        )
        if not is_exit or confidence < threshold:
            return
        with self._lock:
            if (
                self._session_ending
                or self._runtime_closed()
                or activity_generation != self._user_activity_generation
            ):
                return
            self._session_ending = True
        self._emit_interrupt("user_farewell")
        self._send_action({
            "type": "say",
            "text": self.config["farewell_text"],
            "then": "end",
            "reason": "user_farewell",
        }, request_id)

    def _handle_reminder_capture(self, data: Dict[str, Any], request_id: str) -> None:
        if not self.config["reminder_capture_enabled"]:
            return
        text = str(data.get("text") or "").strip()
        if not text or len(text) > 500:
            logger.warning(f"[{self._session_id()}] invalid reminder_capture text")
            return
        with self._lock:
            if self._session_ending or self._reminder_capture_inflight:
                return
            self._reminder_capture_inflight = True
        try:
            future = _EVENT_EXECUTOR.submit(self._semantics.extract_reminder, text)
        except RuntimeError as exc:
            with self._lock:
                self._reminder_capture_inflight = False
            logger.opt(exception=exc).warning(f"[{self._session_id()}] reminder extraction submit failed")
            return
        future.add_done_callback(lambda done: self._finish_reminder_capture(done, request_id))

    def _finish_reminder_capture(self, future: Future, request_id: str) -> None:
        with self._lock:
            self._reminder_capture_inflight = False
        try:
            reminder = future.result()
        except Exception as exc:
            logger.opt(exception=exc).warning(f"[{self._session_id()}] reminder extraction failed")
            reminder = None
        with self._lock:
            if self._session_ending or self._runtime_closed():
                return
        if reminder is None:
            self._send_action({
                "type": "say",
                "text": self.config["reminder_failure_text"],
                "then": "keep",
                "reason": "reminder_capture_failed",
            }, request_id)
            return
        action_id = f"rem-{uuid.uuid4().hex}"
        action = {"type": "reminder.create", "action_id": action_id, **reminder}
        with self._lock:
            while len(self._pending_reminder_actions) >= 32:
                oldest = next(iter(self._pending_reminder_actions))
                self._pending_reminder_actions.pop(oldest, None)
            self._pending_reminder_actions[action_id] = reminder["title"]
        self._send_action(action, request_id)

    def _handle_reminder_ack(self, data: Dict[str, Any], request_id: str) -> None:
        raw_action_id = data.get("action_id")
        action_id = str(raw_action_id).strip() if raw_action_id is not None else ""
        with self._lock:
            if action_id and action_id in self._pending_reminder_actions:
                self._pending_reminder_actions.pop(action_id, None)
            elif not action_id and len(self._pending_reminder_actions) == 1:
                only_action_id = next(iter(self._pending_reminder_actions))
                self._pending_reminder_actions.pop(only_action_id, None)
            else:
                logger.info(
                    f"[{self._session_id()}] unmatched reminder_ack ignored: "
                    f"action_id={action_id or '-'}"
                )
                return
            if self._session_ending:
                return
        raw_ok = data.get("ok")
        ok = raw_ok is True or str(raw_ok).strip().lower() == "true"
        self._send_action({
            "type": "say",
            "text": self.config[
                "reminder_success_text" if ok else "reminder_failure_text"
            ],
            "then": "keep",
            "reason": "reminder_create_ack" if ok else "reminder_create_failed",
        }, request_id)

    def _handle_reminder_due(self, data: Dict[str, Any], request_id: str) -> None:
        with self._lock:
            if self._session_ending:
                return
        text = str(data.get("speak_text") or data.get("title") or "").strip()
        if not text or len(text) > 300:
            logger.warning(f"[{self._session_id()}] invalid reminder_due payload")
            return
        priority = str(data.get("priority") or "normal").strip().lower()
        kind = str(data.get("kind") or "custom").strip().lower()
        interrupt = priority in {"critical", "high"} or kind in {"medication", "health"}
        if interrupt:
            self._emit_interrupt("reminder_due")
        action = {
            "type": "say",
            "text": text,
            "then": "keep",
            "reason": "reminder_due",
            "delivery": "interrupt" if interrupt else "after_current",
        }
        for key in ("reminder_id", "occurrence_id", "kind", "priority"):
            if data.get(key) is not None:
                action[key] = data[key]
        self._send_action(action, request_id)
