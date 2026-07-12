from __future__ import annotations

import base64
import binascii
import os
from typing import Any, Callable, Dict, Optional

from ..responses import V1HTTPException
from .recognizer import (
    BailianSpeechRecognizer,
    RecognitionConfig,
    SpeechRecognitionError,
    SpeechRecognitionTimeout,
)
from .schemas import ASRTranscribeRequest


DEFAULT_MODEL = "fun-asr-realtime"
DEFAULT_WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_MAX_AUDIO_SECONDS = 30
PCM_BYTES_PER_SECOND = 16000 * 1 * 2


class ASRService:
    def __init__(
        self,
        recognizer_factory: Callable[[RecognitionConfig], BailianSpeechRecognizer] = BailianSpeechRecognizer,
    ):
        self.recognizer_factory = recognizer_factory

    def transcribe(self, payload: ASRTranscribeRequest, app_state: Optional[Any] = None) -> Dict:
        audio_bytes = self._decode_audio(payload.audio_base64)
        config = self._recognition_config(app_state)
        try:
            text = self.recognizer_factory(config).transcribe(audio_bytes)
        except SpeechRecognitionTimeout as exc:
            raise V1HTTPException("UPSTREAM_TIMEOUT", "speech recognition timed out", 502) from exc
        except SpeechRecognitionError as exc:
            raise V1HTTPException("UPSTREAM_ERROR", f"failed to recognize speech: {exc}", 502) from exc

        return {
            "text": text,
            "model_name": config.model_name,
            "audio_format": payload.audio_format,
            "sample_rate": payload.sample_rate,
            "channels": payload.channels,
            "duration_ms": len(audio_bytes) * 1000 // PCM_BYTES_PER_SECOND,
        }

    def _decode_audio(self, encoded_audio: str) -> bytes:
        try:
            audio_bytes = base64.b64decode(encoded_audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise V1HTTPException("INVALID_AUDIO", "audio_base64 is invalid", 400) from exc
        if not audio_bytes:
            raise V1HTTPException("INVALID_AUDIO", "audio data is empty", 400)
        if len(audio_bytes) % 2 != 0:
            raise V1HTTPException("INVALID_AUDIO", "PCM audio byte length must be even", 400)
        if len(audio_bytes) > self._max_audio_bytes():
            raise V1HTTPException("AUDIO_TOO_LARGE", "audio duration exceeds the configured limit", 413)
        return audio_bytes

    def _recognition_config(self, app_state: Optional[Any]) -> RecognitionConfig:
        handler_config = self._resolve_asr_config(app_state)
        api_key = self._config_value(handler_config, "api_key", "DASHSCOPE_API_KEY", "")
        if not api_key:
            raise V1HTTPException("INTERNAL_ERROR", "ASR api key is not configured", 500)
        return RecognitionConfig(
            api_key=api_key,
            model_name=self._config_value(handler_config, "model_name", "V1_ASR_MODEL", DEFAULT_MODEL),
            semantic_punctuation_enabled=bool(handler_config.get("semantic_punctuation_enabled", False)),
            language_hints=handler_config.get("language_hints"),
            base_websocket_url=self._config_value(
                handler_config, "base_websocket_url", "V1_ASR_WEBSOCKET_URL", DEFAULT_WEBSOCKET_URL
            ),
            timeout_seconds=self._positive_float("V1_ASR_TIMEOUT_SECONDS", 30.0),
        )

    @staticmethod
    def _resolve_asr_config(app_state: Optional[Any]) -> Dict:
        engine_config = getattr(app_state, "open_avatar_chat_engine_config", None)
        handler_configs = getattr(engine_config, "handler_configs", None) or {}
        for handler_config in handler_configs.values():
            module = str(handler_config.get("module", ""))
            if module.endswith("asr/bailian_asr/asr_handler_bailian"):
                return handler_config
        return handler_configs.get("BailianASR", {}) or {}

    @staticmethod
    def _config_value(config: Dict, key: str, env_name: str, fallback: str) -> str:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
        value = config.get(key)
        return str(value).strip() if value is not None and str(value).strip() else fallback

    @staticmethod
    def _positive_float(env_name: str, fallback: float) -> float:
        try:
            return max(1.0, float(os.getenv(env_name, str(fallback))))
        except ValueError:
            return fallback

    @classmethod
    def _max_audio_bytes(cls) -> int:
        seconds = cls._positive_float("V1_ASR_MAX_AUDIO_SECONDS", DEFAULT_MAX_AUDIO_SECONDS)
        return int(seconds * PCM_BYTES_PER_SECOND)
