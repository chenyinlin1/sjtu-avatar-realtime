"""LLM-backed semantic resolution for speaker session events."""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger


class SessionEventSemanticResolver:
    """Resolve exit intent and reminder fields without owning event state."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        runtime_snapshot: Callable[[], Dict[str, Any]],
        session_id: Callable[[], Optional[str]],
    ):
        self._config = config
        self._runtime_snapshot = runtime_snapshot
        self._session_id = session_id

    def _new_client(self):
        api_key = (
            self._config.get("exit_intent_api_key")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
        )
        if not api_key:
            return None
        from openai import OpenAI
        return OpenAI(
            api_key=api_key,
            base_url=self._config["exit_intent_api_url"],
            timeout=float(self._config["exit_intent_timeout_seconds"]),
        )

    def _call_model(
        self, system_prompt: str, user_text: str, max_tokens: int
    ) -> Optional[Dict[str, Any]]:
        client = self._new_client()
        if client is None:
            logger.warning(f"[{self._session_id()}] session semantic model skipped: API key unavailable")
            return None
        try:
            response = client.chat.completions.create(
                model=self._config["exit_intent_model_name"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                stream=False,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content or ""
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            return json.loads(match.group(0)) if match else None
        finally:
            client.close()

    def classify_exit_intent(self, text: str) -> Optional[tuple[bool, float]]:
        result = self._call_model(
            "判断用户是否明确要求结束当前语音会话。"
            "告别、去睡觉、让助手退出属于结束；"
            "讨论退出这个词、转述他人或否定结束不属于。"
            "只输出JSON：{\"is_exit\":true或false,\"confidence\":0到1}。",
            text,
            80,
        )
        if not result:
            return None
        raw_exit = result.get("is_exit")
        is_exit = raw_exit is True or str(raw_exit).strip().lower() == "true"
        confidence = float(result.get("confidence", 0.0))
        return is_exit, max(0.0, min(1.0, confidence))

    def extract_reminder(self, text: str) -> Optional[Dict[str, Any]]:
        runtime = self._runtime_snapshot()
        device_info = runtime.get("device_info") or {}
        timezone_name = str(device_info.get("timezone") or "Asia/Shanghai")
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "Asia/Shanghai"
            timezone = ZoneInfo(timezone_name)
        now = datetime.now(timezone)
        try:
            result = self._call_model(
                f"当前时间是{now.isoformat()}，设备时区是{timezone_name}。"
                "从用户原话抽取一次性或简单重复提醒。只输出JSON，字段为："
                "title字符串、remind_at epoch毫秒整数、repeat为none/daily/weekly、"
                "speak_text字符串、confidence为0到1。不要修改用药方案。",
                text,
                180,
            )
            if not result:
                return None
            title = str(result.get("title") or "").strip()
            speak_text = str(result.get("speak_text") or title).strip()
            repeat = str(result.get("repeat") or "none").strip().lower()
            confidence = float(result.get("confidence", 0.0))
            remind_at = int(result.get("remind_at"))
            if remind_at < 100000000000:
                remind_at *= 1000
            if not title or len(title) > 100 or not speak_text or len(speak_text) > 200:
                return None
            if repeat not in {"none", "daily", "weekly"}:
                return None
            if remind_at < int(time.time() * 1000) - 30000:
                return None
            if confidence < float(self._config["reminder_extract_confidence"]):
                return None
            return {
                "kind": "custom",
                "title": title,
                "remind_at": remind_at,
                "repeat": repeat,
                "speak_text": speak_text,
                "timezone": timezone_name,
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
