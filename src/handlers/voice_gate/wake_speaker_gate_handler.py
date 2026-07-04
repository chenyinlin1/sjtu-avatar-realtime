from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, cast

import numpy as np
from pydantic import BaseModel, Field

try:
    from loguru import logger
except Exception:  # pragma: no cover - target runtime has loguru.
    import logging

    logger = logging.getLogger(__name__)

from chat_engine.common.handler_base import (
    HandlerBase,
    HandlerBaseInfo,
    HandlerDataInfo,
    HandlerDetail,
)
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_engine_config_data import (
    ChatEngineConfigModel,
    HandlerBaseConfigModel,
)
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import ChatSignalSourceType, ChatSignalType
from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundle,
    DataBundleDefinition,
    DataBundleEntry,
)
from engine_utils.directory_info import DirectoryInfo

from handlers.voice_gate.features import (
    audio_array_to_float32,
    audio_array_to_pcm16,
    pcm16_stats,
)
from handlers.voice_gate.speaker_verifier import DEFAULT_SPEAKER_MODEL, SpeakerVerifier
from handlers.voice_gate.text_utils import (
    is_exit_intent,
    is_wake_only_text,
    looks_like_asr_noise,
    strip_wake_word,
)
from handlers.voice_gate.voice_gate import (
    VoiceFeatures,
    VoiceGateConfig,
    VoiceSessionGate,
    VoiceSessionState,
)
from handlers.voice_gate.wakeword import (
    DEFAULT_KWS_MODEL,
    DEFAULT_KWS_MODEL_URL,
    KeywordSpotterConfig,
    LazyWakeDetector,
    NullWakeDetector,
    WakeDetector,
    ensure_keyword_spotter_model,
)


WAKE_ONLY_RESPONSE_TEXT = "嗯，我在，有什么可以帮你？"


class WakeSpeakerGateConfig(HandlerBaseConfigModel, BaseModel):
    wake_word: str = Field(default="小伴小伴")
    sample_rate: int = Field(default=16000)
    speaker_gate_enabled: bool = Field(default=True)

    min_duration_ms: int = Field(default=300)
    min_rms: float = Field(default=0.01)
    min_snr_db: float = Field(default=8.0)
    speaker_threshold: float = Field(default=0.45)
    speaker_borderline_threshold: float = Field(default=0.40)
    speaker_soft_threshold: float = Field(default=0.45)
    trusted_speaker_profile_threshold: float = Field(default=0.55)
    rejected_speaker_learn_max_score: float = Field(default=0.35)
    rejected_speaker_profile_threshold: float = Field(default=0.85)
    rejected_speaker_margin: float = Field(default=0.08)
    rejected_speaker_profile_max: int = Field(default=6)
    speaker_grace_ms: int = Field(default=12000)
    speaker_grace_segments: int = Field(default=2)
    idle_timeout_ms: int = Field(default=90000)
    non_target_timeout_ms: int = Field(default=20000)

    default_snr_db: float = Field(default=18.0)
    wake_enrollment_ms: int = Field(default=1200)
    interrupt_probe_ms: int = Field(default=700)
    wake_only_response_text: str = Field(default=WAKE_ONLY_RESPONSE_TEXT)

    kws_enabled: bool = Field(default=True)
    kws_model_dir: str = Field(default=f"models/{DEFAULT_KWS_MODEL}")
    kws_auto_download: bool = Field(default=True)
    kws_model_url: str = Field(default=DEFAULT_KWS_MODEL_URL)
    kws_provider: str = Field(default="cpu")

    speaker_model_name: str = Field(default=DEFAULT_SPEAKER_MODEL)
    speaker_device: str = Field(default="cpu")


@dataclass(slots=True)
class PendingSegment:
    features: VoiceFeatures
    raw_text: str = ""
    strip_wake_word: bool = False
    interrupt_emitted: bool = False


class WakeSpeakerGateContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config: WakeSpeakerGateConfig = WakeSpeakerGateConfig()
        self.voice_gate = VoiceSessionGate()
        self.wake_detector: WakeDetector = NullWakeDetector()

        self.recent_mic_pcm = bytearray()
        self.pending_strip_wake_word = False

        self.current_audio_stream_key = None
        self.current_audio_chunks: list[np.ndarray] = []
        self.current_strip_wake_word = False
        self.current_interrupt_emitted = False
        self.current_probe_done = False

        self.pending_segments: dict[str, PendingSegment] = {}

    def remember_mic_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        self.recent_mic_pcm.extend(pcm)
        max_bytes = round(self.config.sample_rate * self.config.wake_enrollment_ms / 1000) * 2
        if max_bytes <= 0:
            self.recent_mic_pcm.clear()
            return
        overflow = len(self.recent_mic_pcm) - max_bytes
        if overflow > 0:
            del self.recent_mic_pcm[:overflow]


class WakeSpeakerGateHandler(HandlerBase):
    def __init__(self):
        super().__init__()
        self.config = WakeSpeakerGateConfig()
        self._speaker_verifier: SpeakerVerifier | None = None

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            name="WakeSpeakerGate",
            config_model=WakeSpeakerGateConfig,
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[HandlerBaseConfigModel] = None):
        if isinstance(handler_config, WakeSpeakerGateConfig):
            self.config = handler_config
        if self.config.kws_enabled and self.config.kws_auto_download:
            model_dir = _resolve_project_path(self.config.kws_model_dir)
            logger.info(f"WakeSpeakerGate: ensuring KWS model at {model_dir}")
            ensure_keyword_spotter_model(
                model_dir,
                model_url=self.config.kws_model_url,
            )

    def create_context(
        self,
        session_context: SessionContext,
        handler_config: Optional[HandlerBaseConfigModel] = None,
    ) -> HandlerContext:
        config = handler_config if isinstance(handler_config, WakeSpeakerGateConfig) else self.config
        context = WakeSpeakerGateContext(session_context.session_info.session_id)
        context.config = config
        context.voice_gate = VoiceSessionGate(_voice_gate_config(config))
        context.wake_detector = self._create_wake_detector(config)
        return context

    def start_context(self, session_context: SessionContext, handler_context: HandlerContext):
        pass

    def get_handler_detail(self, session_context: SessionContext, context: HandlerContext) -> HandlerDetail:
        audio_definition = DataBundleDefinition()
        audio_definition.add_entry(DataBundleEntry.create_audio_entry("human_audio", 1, 16000))

        text_definition = DataBundleDefinition()
        text_definition.add_entry(DataBundleEntry.create_text_entry("human_text"))

        avatar_text_definition = DataBundleDefinition()
        avatar_text_definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))

        return HandlerDetail(
            inputs=[
                HandlerDataInfo(type=ChatDataType.MIC_AUDIO),
                HandlerDataInfo(type=ChatDataType.HUMAN_DUPLEX_AUDIO),
                HandlerDataInfo(type=ChatDataType.HUMAN_DUPLEX_TEXT),
            ],
            outputs=[
                HandlerDataInfo(type=ChatDataType.HUMAN_AUDIO, definition=audio_definition),
                HandlerDataInfo(type=ChatDataType.HUMAN_TEXT, definition=text_definition),
                HandlerDataInfo(type=ChatDataType.AVATAR_TEXT, definition=avatar_text_definition),
            ],
        )

    def handle(
        self,
        context: HandlerContext,
        inputs: ChatData,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
    ):
        ctx = cast(WakeSpeakerGateContext, context)
        if inputs.type == ChatDataType.MIC_AUDIO:
            self._handle_mic_audio(ctx, inputs)
        elif inputs.type == ChatDataType.HUMAN_DUPLEX_AUDIO:
            self._handle_duplex_audio(ctx, inputs, output_definitions)
        elif inputs.type == ChatDataType.HUMAN_DUPLEX_TEXT:
            self._handle_duplex_text(ctx, inputs, output_definitions)

    def _handle_mic_audio(self, context: WakeSpeakerGateContext, inputs: ChatData) -> None:
        audio = _main_audio(inputs)
        pcm = audio_array_to_pcm16(audio)
        context.remember_mic_pcm(pcm)
        if not context.config.kws_enabled:
            return

        keyword = context.wake_detector.accept_pcm16(pcm)
        if not keyword:
            return

        self._lock_wake_from_kws(context, keyword)

    def _handle_duplex_audio(
        self,
        context: WakeSpeakerGateContext,
        inputs: ChatData,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
    ) -> None:
        audio = audio_array_to_float32(_main_audio(inputs))
        if audio.size == 0:
            return

        stream_key = inputs.stream_id.key if inputs.stream_id else None
        if stream_key != context.current_audio_stream_key:
            context.current_audio_stream_key = stream_key
            context.current_audio_chunks = []
            context.current_strip_wake_word = context.pending_strip_wake_word
            context.pending_strip_wake_word = False
            context.current_interrupt_emitted = False
            context.current_probe_done = False

        context.current_audio_chunks.append(audio.copy())

        if (
            not context.current_probe_done
            and context.voice_gate.state != VoiceSessionState.STANDBY
            and self._is_avatar_speaking(context)
        ):
            self._maybe_emit_probe_interrupt(context)

        if inputs.is_last_data:
            self._finish_audio_segment(context, inputs, output_definitions)

    def _handle_duplex_text(
        self,
        context: WakeSpeakerGateContext,
        inputs: ChatData,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
    ) -> None:
        raw_text = _main_text(inputs).strip()
        if not raw_text:
            return

        metadata = inputs.data.metadata if inputs.data is not None else {}
        segment_id = metadata.get("voice_gate_segment_id")
        segment = context.pending_segments.pop(segment_id, None) if segment_id else None
        if segment is None:
            logger.debug("WakeSpeakerGate: ignoring ASR text without matching gated segment")
            return

        text = strip_wake_word(raw_text, context.config.wake_word) if segment.strip_wake_word else raw_text
        wake_only = segment.strip_wake_word and is_wake_only_text(raw_text, context.config.wake_word)
        if wake_only or looks_like_asr_noise(text, context.config.wake_word):
            if wake_only:
                self._submit_avatar_text(
                    context,
                    output_definitions,
                    context.config.wake_only_response_text,
                    metadata={"voice_gate_reason": "wake_only_response", "raw_text": raw_text},
                )
            return

        if is_exit_intent(text):
            context.voice_gate.reset()
            logger.info("WakeSpeakerGate: user exit intent detected, returning to standby")
            return

        decision = context.voice_gate.handle_segment(
            segment.features,
            text,
            now_ms=_now_ms(),
            tts_playing=self._is_avatar_speaking(context),
            speaker_gate_enabled=context.config.speaker_gate_enabled,
        )
        logger.info(
            f"WakeSpeakerGate text decision: accepted={decision.accepted} "
            f"reason={decision.reason} speaker_score={decision.speaker_score} text={text[:50]}"
        )
        if not decision.accepted:
            return

        if decision.allow_interrupt and not segment.interrupt_emitted:
            self._emit_interrupt(context, text, reason="wake_speaker_text_interrupt")

        self._submit_human_text(
            context,
            output_definitions,
            text,
            metadata={
                "voice_gate_reason": decision.reason,
                "voice_gate_speaker_score": decision.speaker_score,
                "voice_gate_raw_text": raw_text,
            },
        )

    def _finish_audio_segment(
        self,
        context: WakeSpeakerGateContext,
        inputs: ChatData,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
    ) -> None:
        if not context.current_audio_chunks:
            return

        audio = np.concatenate(context.current_audio_chunks, axis=0).astype(np.float32, copy=False)
        context.current_audio_chunks = []

        if context.voice_gate.state == VoiceSessionState.STANDBY:
            logger.info("WakeSpeakerGate: drop audio while standby; wake word not detected")
            return

        features = self._features_for_audio(context, audio)
        reject_reason = context.voice_gate.quality_reject_reason(features)
        if reject_reason is not None:
            logger.info(f"WakeSpeakerGate: audio quality rejected reason={reject_reason}")
            return

        speaker_score = context.voice_gate.speaker_score(features.embedding)
        if not context.voice_gate.can_try_asr_for_speaker_score(
            speaker_score,
            _now_ms(),
            speaker_gate_enabled=context.config.speaker_gate_enabled,
        ):
            decision = context.voice_gate.handle_segment(
                features,
                "",
                now_ms=_now_ms(),
                tts_playing=self._is_avatar_speaking(context),
                speaker_gate_enabled=context.config.speaker_gate_enabled,
            )
            logger.info(
                f"WakeSpeakerGate: speaker rejected before ASR "
                f"reason={decision.reason} score={decision.speaker_score}"
            )
            return

        segment_id = uuid.uuid4().hex
        context.pending_segments[segment_id] = PendingSegment(
            features=features,
            strip_wake_word=context.current_strip_wake_word,
            interrupt_emitted=context.current_interrupt_emitted,
        )
        metadata = {
            "voice_gate_segment_id": segment_id,
            "voice_gate_strip_wake_word": context.current_strip_wake_word,
            "voice_gate_speaker_score": speaker_score,
            "voice_gate_interrupt_emitted": context.current_interrupt_emitted,
        }
        self._submit_human_audio(context, output_definitions, audio, inputs, metadata=metadata)

        context.current_strip_wake_word = False
        context.current_interrupt_emitted = False
        context.current_probe_done = False

    def _maybe_emit_probe_interrupt(self, context: WakeSpeakerGateContext) -> None:
        audio = np.concatenate(context.current_audio_chunks, axis=0).astype(np.float32, copy=False)
        duration_ms = round(audio.shape[0] / context.config.sample_rate * 1000)
        if duration_ms < context.config.interrupt_probe_ms:
            return

        context.current_probe_done = True
        features = self._features_for_audio(context, audio)
        if context.voice_gate.quality_reject_reason(features) is not None:
            return
        speaker_score = context.voice_gate.speaker_score(features.embedding)
        if not context.voice_gate.can_try_asr_for_speaker_score(
            speaker_score,
            _now_ms(),
            speaker_gate_enabled=context.config.speaker_gate_enabled,
        ):
            return
        context.current_interrupt_emitted = True
        self._emit_interrupt(context, "", reason="wake_speaker_voice_interrupt")

    def _submit_human_audio(
        self,
        context: WakeSpeakerGateContext,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
        audio: np.ndarray,
        inputs: ChatData,
        *,
        metadata: dict,
    ) -> None:
        output_info = output_definitions.get(ChatDataType.HUMAN_AUDIO)
        if output_info is None or output_info.definition is None:
            logger.warning("WakeSpeakerGate: HUMAN_AUDIO output definition not found")
            return

        output = DataBundle(output_info.definition)
        output.set_main_data(np.expand_dims(audio, axis=0))
        for key, value in metadata.items():
            output.add_meta(key, value)

        streamer = context.data_submitter.get_streamer(ChatDataType.HUMAN_AUDIO) if context.data_submitter else None
        if streamer is None:
            logger.warning("WakeSpeakerGate: no HUMAN_AUDIO streamer")
            return

        if streamer.current_stream is None:
            source_streams = [inputs.stream_id] if inputs.stream_id else []
            streamer.new_stream(source_streams, name="wake_speaker_gate")
        if streamer.current_stream is not None:
            streamer.current_stream.update_inheritable_metadata(metadata, inherit=True)

        output_chat_data = ChatData(type=ChatDataType.HUMAN_AUDIO, data=output, is_last_data=True)
        if inputs.is_timestamp_valid():
            output_chat_data.timestamp = inputs.timestamp
        streamer.stream_data(output_chat_data, finish_stream=True)

    def _submit_human_text(
        self,
        context: WakeSpeakerGateContext,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
        text: str,
        *,
        metadata: dict,
    ) -> None:
        output_info = output_definitions.get(ChatDataType.HUMAN_TEXT)
        if output_info is None or output_info.definition is None:
            logger.warning("WakeSpeakerGate: HUMAN_TEXT output definition not found")
            return
        output = DataBundle(output_info.definition)
        output.set_main_data(text)
        for key, value in metadata.items():
            output.add_meta(key, value)
        context.submit_data(ChatData(type=ChatDataType.HUMAN_TEXT, data=output), finish_stream=True)

    def _submit_avatar_text(
        self,
        context: WakeSpeakerGateContext,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
        text: str,
        *,
        metadata: dict,
    ) -> None:
        output_info = output_definitions.get(ChatDataType.AVATAR_TEXT)
        if output_info is None or output_info.definition is None:
            logger.warning("WakeSpeakerGate: AVATAR_TEXT output definition not found")
            return
        output = DataBundle(output_info.definition)
        output.set_main_data(text)
        for key, value in metadata.items():
            output.add_meta(key, value)
        context.submit_data(ChatData(type=ChatDataType.AVATAR_TEXT, data=output), finish_stream=True)

    def _lock_wake_from_kws(self, context: WakeSpeakerGateContext, keyword: str) -> None:
        wake_profile_pcm = bytes(context.recent_mic_pcm)
        if not wake_profile_pcm:
            logger.info("WakeSpeakerGate: KWS hit but wake enrollment buffer is empty")
            return

        features = self._features_for_pcm(context, wake_profile_pcm)
        decision = context.voice_gate.on_wake(features, now_ms=_now_ms())
        logger.info(
            f"WakeSpeakerGate: KWS hit keyword={keyword} accepted={decision.accepted} "
            f"reason={decision.reason}"
        )
        if not decision.accepted:
            return

        context.pending_strip_wake_word = True
        if context.current_audio_stream_key is not None:
            context.current_strip_wake_word = True

    def _features_for_audio(self, context: WakeSpeakerGateContext, audio: np.ndarray) -> VoiceFeatures:
        return self._features_for_pcm(context, audio_array_to_pcm16(audio))

    def _features_for_pcm(self, context: WakeSpeakerGateContext, pcm: bytes) -> VoiceFeatures:
        stats = pcm16_stats(pcm, sample_rate=context.config.sample_rate)
        embedding = self._speaker(context.config).extract_embedding(pcm)
        return VoiceFeatures(
            duration_ms=stats.duration_ms,
            rms=stats.rms,
            snr_db=context.config.default_snr_db,
            embedding=embedding,
            is_echo=False,
        )

    def _speaker(self, config: WakeSpeakerGateConfig) -> SpeakerVerifier:
        if self._speaker_verifier is None:
            self._speaker_verifier = SpeakerVerifier(
                model_name=config.speaker_model_name,
                device=config.speaker_device,
            )
        return self._speaker_verifier

    def _create_wake_detector(self, config: WakeSpeakerGateConfig) -> WakeDetector:
        if not config.kws_enabled:
            return NullWakeDetector()
        model_dir = _resolve_project_path(config.kws_model_dir)
        prepare_model = None
        if config.kws_auto_download:
            prepare_model = lambda: ensure_keyword_spotter_model(
                model_dir,
                model_url=config.kws_model_url,
            )
        return LazyWakeDetector(
            lambda: KeywordSpotterConfig.from_model_dir(model_dir, provider=config.kws_provider),
            prepare_model=prepare_model,
        )

    def _emit_interrupt(self, context: WakeSpeakerGateContext, text: str, *, reason: str) -> None:
        logger.info(f"WakeSpeakerGate: emitting interrupt reason={reason} text={text[:50]}")
        context.emit_signal(
            ChatSignal(
                type=ChatSignalType.INTERRUPT,
                source_type=ChatSignalSourceType.HANDLER,
                signal_data={
                    "reason": reason,
                    "trigger_text": text,
                },
            )
        )

    def _is_avatar_speaking(self, context: WakeSpeakerGateContext) -> bool:
        if context.stream_manager is not None:
            for stream in context.stream_manager.get_active_streams():
                if stream.identity.data_type in (
                    ChatDataType.AVATAR_TEXT,
                    ChatDataType.AVATAR_AUDIO,
                    ChatDataType.CLIENT_PLAYBACK,
                ):
                    return True
        if context.session_history is not None:
            return bool(context.session_history.get_active_avatar_streams())
        return False

    def destroy_context(self, context: HandlerContext):
        pass


def _voice_gate_config(config: WakeSpeakerGateConfig) -> VoiceGateConfig:
    return VoiceGateConfig(
        min_duration_ms=config.min_duration_ms,
        min_rms=config.min_rms,
        min_snr_db=config.min_snr_db,
        speaker_threshold=config.speaker_threshold,
        speaker_borderline_threshold=config.speaker_borderline_threshold,
        speaker_soft_threshold=config.speaker_soft_threshold,
        trusted_speaker_profile_threshold=config.trusted_speaker_profile_threshold,
        rejected_speaker_learn_max_score=config.rejected_speaker_learn_max_score,
        rejected_speaker_profile_threshold=config.rejected_speaker_profile_threshold,
        rejected_speaker_margin=config.rejected_speaker_margin,
        rejected_speaker_profile_max=config.rejected_speaker_profile_max,
        speaker_grace_ms=config.speaker_grace_ms,
        speaker_grace_segments=config.speaker_grace_segments,
        idle_timeout_ms=config.idle_timeout_ms,
        non_target_timeout_ms=config.non_target_timeout_ms,
    )


def _main_audio(inputs: ChatData) -> np.ndarray | None:
    if inputs.data is None:
        return None
    data = inputs.data.get_main_data()
    return data if isinstance(data, np.ndarray) else None


def _main_text(inputs: ChatData) -> str:
    if inputs.data is None:
        return ""
    data = inputs.data.get_main_data()
    return data if isinstance(data, str) else ""


def _resolve_project_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path(DirectoryInfo.get_project_dir()) / path


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


handler_class = WakeSpeakerGateHandler
