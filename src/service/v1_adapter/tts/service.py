from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

from ..personas.runtime import PersonaRuntimeError, PersonaRuntimeResolver
from ..responses import V1HTTPException
from .schemas import TTSSynthesizeRequest
from .synthesizer import BailianSpeechSynthesizer, SpeechSynthesisError


COSYVOICE_V35_FLASH_MODEL = "cosyvoice-v3.5-flash"
COSYVOICE_V3_FLASH_MODEL = "cosyvoice-v3-flash"
DEFAULT_VOICE = "longanhuan_v3"
DEFAULT_MODEL = COSYVOICE_V35_FLASH_MODEL


class TTSService:
    def __init__(self, persona_resolver: Optional[PersonaRuntimeResolver] = None):
        self.persona_resolver = persona_resolver or PersonaRuntimeResolver()

    def synthesize(self, payload: TTSSynthesizeRequest, app_state: Optional[Any] = None) -> Dict:
        tts_config = self._resolve_tts_config(app_state)
        persona_runtime = self._resolve_persona(payload)
        selected = self._select_voice(tts_config, persona_runtime)
        synthesizer = BailianSpeechSynthesizer(
            api_key=selected.get("api_key"),
            timeout_seconds=self._timeout_seconds(),
        )
        try:
            audio = synthesizer.synthesize(
                text=payload.text,
                model=selected["model_name"],
                voice=selected["voice"],
                instruction=selected.get("instruction"),
            )
        except SpeechSynthesisError as exc:
            message = str(exc)
            code = "UPSTREAM_TIMEOUT" if "timeout" in message.lower() else "UPSTREAM_ERROR"
            raise V1HTTPException(code, f"failed to synthesize speech: {message}", 502) from exc

        return {
            "audio_base64": base64.b64encode(audio.wav_bytes).decode("ascii"),
            "audio_format": "wav",
            "sample_rate": audio.sample_rate,
            "channels": audio.channels,
            "sample_width_bytes": audio.sample_width_bytes,
            "duration_ms": audio.duration_ms,
            "model_name": selected["model_name"],
            "voice": selected["voice"],
            "voice_source": selected["voice_source"],
            "persona_id": persona_runtime.get("persona_id") if persona_runtime else None,
        }

    def _resolve_persona(self, payload: TTSSynthesizeRequest) -> Optional[Dict]:
        try:
            return self.persona_resolver.resolve(
                persona_id=payload.persona_id,
                elder_id=payload.elder_id,
                tenant_id=payload.tenant_id,
            )
        except PersonaRuntimeError as exc:
            if exc.code == "PERSONA_NOT_FOUND":
                raise V1HTTPException("PERSONA_NOT_FOUND", exc.message, 404) from exc
            if exc.code == "PERSONA_NOT_OWNED":
                raise V1HTTPException("FORBIDDEN", exc.message, 403) from exc
            raise V1HTTPException("INTERNAL_ERROR", exc.message, 500) from exc

    def _select_voice(self, tts_config: Dict, persona_runtime: Optional[Dict]) -> Dict:
        voice = self._config_value(tts_config, "voice", "V1_TTS_DEFAULT_VOICE", DEFAULT_VOICE)
        model_name = self._config_value(tts_config, "model_name", "V1_TTS_DEFAULT_MODEL", DEFAULT_MODEL)
        instruction = self._config_value(tts_config, "instruction", "V1_TTS_DEFAULT_INSTRUCTION", None)
        api_key = self._config_value(tts_config, "api_key", "DASHSCOPE_API_KEY", None)
        voice_source = "default"

        if persona_runtime and persona_runtime.get("voice_id"):
            voice = persona_runtime["voice_id"]
            model_name = persona_runtime.get("voice_model_name") or model_name
            voice_source = "persona"
        elif model_name == COSYVOICE_V35_FLASH_MODEL:
            model_name = COSYVOICE_V3_FLASH_MODEL

        return {
            "voice": voice,
            "model_name": model_name,
            "instruction": instruction,
            "api_key": api_key,
            "voice_source": voice_source,
        }

    @staticmethod
    def _resolve_tts_config(app_state: Optional[Any]) -> Dict:
        engine_config = getattr(app_state, "open_avatar_chat_engine_config", None)
        handler_configs = getattr(engine_config, "handler_configs", None) or {}
        for handler_config in handler_configs.values():
            module = str(handler_config.get("module", ""))
            if module.endswith("tts_handler_cosyvoice_bailian"):
                return handler_config
        return handler_configs.get("CosyVoice", {}) or {}

    @staticmethod
    def _config_value(config: Dict, key: str, env_name: str, fallback: Optional[str]) -> Optional[str]:
        env_value = os.getenv(env_name)
        if env_value is not None and env_value.strip():
            return env_value.strip()
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
        return fallback

    @staticmethod
    def _timeout_seconds() -> float:
        raw_value = os.getenv("V1_TTS_TIMEOUT_SECONDS", "30").strip()
        try:
            return max(1.0, float(raw_value))
        except ValueError:
            return 30.0
