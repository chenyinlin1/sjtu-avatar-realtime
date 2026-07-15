import asyncio
import json
import math
import os
import time
import uuid
import weakref
from typing import Any, Optional, Dict

import numpy as np
# noinspection PyPackageRequirements
from fastrtc import (
    AsyncAudioVideoStreamHandler,
    AudioEmitType,
    VideoEmitType,
    get_current_context,
)
from loguru import logger

from chat_engine.common.client_handler_base import ClientHandlerDelegate, ClientSessionDelegate
from chat_engine.data_models.engine_channel_type import EngineChannelType
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import ChatSignalType, ChatSignalSourceType
from engine_utils.interval_counter import IntervalCounter
from handlers.client.ws_client.ws_message_protocol import (
    ClientEventPayload,
    EchoHumanText,
    EchoTextPayload,
    MessageHeader,
    MessageType,
    serialize_message,
)
from service.v1_adapter.personas.runtime import PersonaRuntimeError, PersonaRuntimeResolver
from engine_utils.conversation_audit_logger import audit_event
from service.rtc_service.session_event_policy import SessionEventPolicy
from service.rtc_service.av_sync import VideoCatchupController, VideoCatchupPlan
from handlers.agent.tools.reminder.pending_actions import get_pending_action_registry


_AV_SYNC_DIAG = os.getenv("AV_SYNC_DIAG", "").lower() in {"1", "true", "yes", "on"}
_MUSIC_STATUS_ACTIVE_STATES = {"loading", "playing", "paused"}
_MUSIC_STATUS_ALLOWED_STATES = _MUSIC_STATUS_ACTIVE_STATES | {"stopped", "ended", "error"}
try:
    _AV_SYNC_DIAG_EVERY = max(1, int(os.getenv("AV_SYNC_DIAG_EVERY", "1")))
except ValueError:
    _AV_SYNC_DIAG_EVERY = 1
try:
    _AV_SYNC_RTP_VIDEO_LEAD_LIMIT_MS = max(
        0.0,
        float(os.getenv("AV_SYNC_RTP_VIDEO_LEAD_LIMIT_MS", "40")),
    )
except ValueError:
    _AV_SYNC_RTP_VIDEO_LEAD_LIMIT_MS = 40.0
_AV_SYNC_RTP_WAIT_INTERVAL_S = 0.005


def _get_h264_encoder_info():
    """Get H.264 encoder info dynamically to avoid circular imports"""
    try:
        from handlers.client.rtc_client import client_handler_rtc
        return client_handler_rtc._selected_h264_encoder, client_handler_rtc._actual_h264_encoder
    except Exception:
        return "unknown", None


class RtcStream(AsyncAudioVideoStreamHandler):
    def __init__(self,
                 session_id: Optional[str],
                 expected_layout="mono",
                 input_sample_rate=16000,
                 output_sample_rate=24000,
                 output_frame_size=480,
                 fps=30,
                 stream_start_delay = 0.5,
                 session_policy_config: Optional[Dict[str, Any]] = None,
                 ):
        super().__init__(
            expected_layout=expected_layout,
            input_sample_rate=input_sample_rate,
            output_sample_rate=output_sample_rate,
            output_frame_size=output_frame_size,
            fps=fps
        )
        self.client_handler_delegate: Optional[ClientHandlerDelegate] = None
        self.client_session_delegate: Optional[ClientSessionDelegate] = None

        self.weak_factory: Optional[weakref.ReferenceType[RtcStream]] = None

        self.session_id = session_id
        self.stream_start_delay = stream_start_delay

        self.chat_channel = None
        self.chat_channel_loop = None
        self.first_audio_emitted = False

        self.quit = asyncio.Event()
        self.last_frame_time = 0

        self.emit_counter = IntervalCounter("emit counter")

        self.start_time = None
        self.timestamp_base = self.input_sample_rate

        self.streams: Dict[str, RtcStream] = {}
        self.owns_session = False
        self._av_diag_audio_emit_seq = 0
        self._av_diag_video_emit_seq = 0
        self._av_diag_audio_samples_total = 0
        self._av_diag_audio_rtp_seq = 0
        self._av_diag_video_rtp_seq = 0
        self._av_rtp_audio_ms: Optional[float] = None
        self._av_rtp_video_ms: Optional[float] = None
        self._av_rtp_video_lead_limit_ms = _AV_SYNC_RTP_VIDEO_LEAD_LIMIT_MS
        self._av_rtp_video_offset_ms = 0.0
        self._av_rtp_video_rebase_pending = False
        self._av_rtp_video_rebase_reason = ""
        self._av_video_catchup_controller = VideoCatchupController(fps=self.fps)
        self._av_video_catchup_plan: Optional[VideoCatchupPlan] = None
        self._av_sync_reset_requested = False
        self._av_sync_reset_reason = ""
        self._av_sync_reset_target_speech_id: Optional[str] = None
        self._av_sync_current_speech_id: Optional[str] = None
        self._input_gate_drop_logged = False
        self._pending_device_info: Optional[tuple[Dict[str, Any], str]] = None

        self.session_event_policy = SessionEventPolicy(
            session_policy_config,
            session_id=lambda: self.session_id,
            send_action=self._send_client_action,
            emit_interrupt=self._emit_policy_interrupt,
            runtime_snapshot=self._session_policy_runtime_snapshot,
            handle_action_ack=self._resolve_action_ack,
        )
        self.session_policy_config = dict(self.session_event_policy.config)

    @property
    def _session_ending(self) -> bool:
        return self.session_event_policy.session_ending

    def note_user_activity(self, source: str = "unknown") -> None:
        self.session_event_policy.note_user_activity(source)

    def _session_policy_runtime_snapshot(self) -> Dict[str, Any]:
        delegate = self.client_session_delegate
        shared_states = getattr(delegate, "shared_states", None)
        return {
            "music_active": bool(getattr(shared_states, "music_player_active", False)),
            "avatar_output_active": self._av_sync_current_speech_id is not None,
            "device_info": getattr(delegate, "device_info", None),
            "closed": self.quit.is_set(),
        }

    def _send_data_channel_json_threadsafe(
        self, name: str, request_id: str, payload: Dict[str, Any]
    ) -> None:
        loop = self.chat_channel_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._send_data_channel_json, name, request_id, payload)
            return
        self._send_data_channel_json(name, request_id, payload)

    def _send_client_action(
        self, action: Dict[str, Any], request_id: Optional[str] = None
    ) -> None:
        action = dict(action)
        action.setdefault("action_id", request_id or f"act-{uuid.uuid4().hex}")
        payload = {
            "stream_key": None,
            "mode": "full_text",
            "text": "",
            "end_of_speech": True,
            "metadata": {"client_action": action},
        }
        self._send_data_channel_json_threadsafe(
            "EchoAvatarText", request_id or str(uuid.uuid4()), payload
        )
        logger.info(
            f"[{self.session_id}] client_action sent: type={action.get('type')} "
            f"action_id={action.get('action_id')}"
        )

    def _emit_policy_interrupt(self, reason: str) -> None:
        if self.client_session_delegate is None:
            return
        self.client_session_delegate.emit_signal(
            ChatSignal(
                type=ChatSignalType.INTERRUPT,
                source_type=ChatSignalSourceType.HANDLER,
                source_name="rtc_session_policy",
                signal_data={"reason": reason},
            )
        )

    def _handle_client_event(self, payload: Dict[str, Any], request_id: str) -> None:
        self.session_event_policy.handle_client_event(payload, request_id)

    def _resolve_action_ack(self, data: Dict[str, Any]) -> str:
        delegate = self.client_session_delegate
        shared_states = getattr(delegate, "shared_states", None)
        registry = get_pending_action_registry(
            shared_states,
            str(self.session_id or "unknown"),
            create=False,
        )
        if registry is None:
            action_id = str(data.get("action_id") or "").strip()
            logger.info(
                f"[{self.session_id}] unknown action_ack ignored without registry: "
                f"action_id={action_id or '-'}"
            )
            return "unknown"
        return registry.resolve(data)

    def request_av_sync_reset(
        self,
        reason: str = "",
        target_speech_id: Optional[str] = None,
    ) -> None:
        """Request stale RTC output cleanup from the emit loop."""
        self._av_sync_reset_requested = True
        self._av_sync_reset_reason = reason or "unspecified"
        self._av_sync_reset_target_speech_id = target_speech_id

    def finish_av_sync_playback(self, reason: str = "") -> None:
        """Clear current playback speech tracking when a CLIENT_PLAYBACK stream ends."""
        previous_speech_id = self._av_sync_current_speech_id
        self._av_sync_reset_requested = False
        self._av_sync_reset_reason = ""
        self._av_sync_reset_target_speech_id = None
        self._av_sync_current_speech_id = None
        if previous_speech_id is not None:
            logger.info(
                f"AV_SYNC_RTC_PLAYBACK_FINISH session={self.session_id} reason={reason or 'unspecified'} "
                f"previous_speech_id={previous_speech_id}"
            )

    def _chat_data_speech_id(self, chat_data: ChatData) -> Optional[str]:
        data = getattr(chat_data, "data", None)
        metadata = getattr(data, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        speech_id = metadata.get("flashhead_speech_id")
        if speech_id is None:
            return None
        return str(speech_id)

    def _should_drop_for_av_sync_speech(self, speech_id: Optional[str]) -> bool:
        return (
            self._av_sync_current_speech_id is not None
            and speech_id != self._av_sync_current_speech_id
        )

    def _get_video_output_queue(self) -> Optional[asyncio.Queue]:
        delegate = self.client_session_delegate
        if delegate is None:
            return None
        output_queues = getattr(delegate, "output_queues", None)
        if not isinstance(output_queues, dict):
            return None
        return output_queues.get(EngineChannelType.VIDEO)

    def _schedule_video_catchup(self, drift_ms: float) -> None:
        if (
            self._av_video_catchup_plan is not None
            or self._av_sync_reset_requested
            or self._av_rtp_video_rebase_pending
        ):
            return

        video_queue = self._get_video_output_queue()
        queue_size = video_queue.qsize() if video_queue is not None else 0
        plan = self._av_video_catchup_controller.observe(
            drift_ms,
            queue_size=queue_size,
            now_s=time.monotonic(),
        )
        if plan is None:
            return

        self._av_video_catchup_plan = plan
        logger.info(
            f"AV_SYNC_RTP_VIDEO_CATCHUP_SCHEDULE session={self.session_id} "
            f"audio_lead_ms={plan.observed_lag_ms:.1f} queue_size={plan.queue_size} "
            f"requested_drop_frames={plan.requested_drop_frames}"
        )

    def _apply_pending_video_catchup(self) -> None:
        plan = self._av_video_catchup_plan
        if plan is None:
            return
        self._av_video_catchup_plan = None

        video_queue = self._get_video_output_queue()
        dropped_frames = 0
        if video_queue is not None:
            while dropped_frames < plan.requested_drop_frames:
                try:
                    video_queue.get_nowait()
                    dropped_frames += 1
                except asyncio.QueueEmpty:
                    break

        self._av_rtp_video_rebase_pending = True
        self._av_rtp_video_rebase_reason = "continuous_audio_lead_catchup"
        remaining_queue_size = video_queue.qsize() if video_queue is not None else 0
        logger.info(
            f"AV_SYNC_RTC_VIDEO_CATCHUP_APPLY session={self.session_id} "
            f"audio_lead_ms={plan.observed_lag_ms:.1f} "
            f"requested_drop_frames={plan.requested_drop_frames} "
            f"dropped_frames={dropped_frames} remaining_queue_size={remaining_queue_size}"
        )

    def _apply_pending_av_sync_reset(self) -> None:
        if not self._av_sync_reset_requested:
            return

        reason = self._av_sync_reset_reason or "unspecified"
        target_speech_id = self._av_sync_reset_target_speech_id
        self._av_sync_reset_requested = False
        self._av_sync_reset_reason = ""
        self._av_sync_reset_target_speech_id = None

        cleared = None
        if self.client_session_delegate is not None:
            clear_data = getattr(self.client_session_delegate, "clear_data", None)
            if clear_data is not None:
                try:
                    cleared = clear_data()
                except Exception as e:
                    logger.opt(exception=e).warning(
                        f"AV_SYNC_RTC_RESET clear_data failed session={self.session_id} reason={reason}"
                    )

        cleared_rtp_audio_queue = False
        clear_rtp_audio_queue = getattr(self, "clear_queue", None)
        if callable(clear_rtp_audio_queue):
            try:
                clear_rtp_audio_queue()
                cleared_rtp_audio_queue = True
            except Exception as e:
                logger.opt(exception=e).warning(
                    f"AV_SYNC_RTC_RESET clear RTP audio queue failed "
                    f"session={self.session_id} reason={reason}"
                )

        self._av_rtp_audio_ms = None
        self._av_rtp_video_ms = None
        self._av_rtp_video_rebase_pending = True
        self._av_rtp_video_rebase_reason = reason
        self._av_video_catchup_plan = None
        self._av_video_catchup_controller.reset()
        self._av_sync_current_speech_id = target_speech_id
        logger.info(
            f"AV_SYNC_RTC_RESET session={self.session_id} reason={reason} "
            f"business_queues={cleared} rtp_audio_queue={cleared_rtp_audio_queue} "
            f"target_speech_id={target_speech_id}"
        )

    def note_audio_rtp_egress(self, media_ms: float, *, codec: str) -> None:
        if self.quit.is_set():
            return
        if self._av_rtp_audio_ms is None or media_ms >= self._av_rtp_audio_ms:
            self._av_rtp_audio_ms = media_ms
        self._av_diag_audio_rtp_seq += 1
        if _AV_SYNC_DIAG and self._av_diag_audio_rtp_seq % _AV_SYNC_DIAG_EVERY == 0:
            logger.info(
                f"AV_SYNC_RTP_AUDIO_EGRESS session={self.session_id} "
                f"mono={time.monotonic():.6f} seq={self._av_diag_audio_rtp_seq} "
                f"media_ms={media_ms:.1f} codec={codec}"
            )

    async def wait_for_video_rtp_egress(self, media_ms: float, *, codec: str) -> float:
        raw_media_ms = media_ms
        media_ms += self._av_rtp_video_offset_ms
        wait_start = time.perf_counter()
        audio_ms = self._av_rtp_audio_ms

        while not self.quit.is_set() and audio_ms is None:
            await asyncio.sleep(_AV_SYNC_RTP_WAIT_INTERVAL_S)
            audio_ms = self._av_rtp_audio_ms

        if self._av_rtp_video_rebase_pending and audio_ms is not None:
            drift_before_rebase_ms = media_ms - audio_ms
            frame_interval_ms = 1000.0 / self.fps if self.fps > 0 else 40.0
            rebase_threshold_ms = max(80.0, frame_interval_ms * 2)
            if drift_before_rebase_ms < -rebase_threshold_ms:
                correction_ms = (
                    math.ceil(-drift_before_rebase_ms / frame_interval_ms)
                    * frame_interval_ms
                )
                old_offset_ms = self._av_rtp_video_offset_ms
                self._av_rtp_video_offset_ms += correction_ms
                media_ms += correction_ms
                logger.info(
                    f"AV_SYNC_RTP_VIDEO_REBASE session={self.session_id} "
                    f"reason={self._av_rtp_video_rebase_reason or 'unspecified'} "
                    f"raw_video_ms={raw_media_ms:.1f} audio_media_ms={audio_ms:.1f} "
                    f"drift_before_ms={drift_before_rebase_ms:.1f} "
                    f"old_offset_ms={old_offset_ms:.1f} correction_ms={correction_ms:.1f} "
                    f"new_offset_ms={self._av_rtp_video_offset_ms:.1f}"
                )
            self._av_rtp_video_rebase_pending = False
            self._av_rtp_video_rebase_reason = ""

        while (
            not self.quit.is_set()
            and audio_ms is not None
            and media_ms - audio_ms > self._av_rtp_video_lead_limit_ms
        ):
            await asyncio.sleep(_AV_SYNC_RTP_WAIT_INTERVAL_S)
            audio_ms = self._av_rtp_audio_ms

        self._av_rtp_video_ms = media_ms
        self._av_diag_video_rtp_seq += 1
        wait_ms = (time.perf_counter() - wait_start) * 1000.0
        drift_ms = media_ms - audio_ms if audio_ms is not None else float("inf")
        self._schedule_video_catchup(drift_ms)
        if _AV_SYNC_DIAG and self._av_diag_video_rtp_seq % _AV_SYNC_DIAG_EVERY == 0:
            logger.info(
                f"AV_SYNC_RTP_VIDEO_EGRESS session={self.session_id} "
                f"mono={time.monotonic():.6f} seq={self._av_diag_video_rtp_seq} "
                f"media_ms={media_ms:.1f} raw_media_ms={raw_media_ms:.1f} "
                f"offset_ms={self._av_rtp_video_offset_ms:.1f} audio_media_ms={audio_ms} "
                f"drift_ms={drift_ms:.1f} wait_ms={wait_ms:.1f} "
                f"limit_ms={self._av_rtp_video_lead_limit_ms:.1f} codec={codec}"
            )
        return media_ms


    # copy is used as create_instance in fastrtc
    def copy(self, **kwargs) -> AsyncAudioVideoStreamHandler:
        try:
            if self.client_handler_delegate is None:
                raise Exception("ClientHandlerDelegate is not set.")

            new_stream = RtcStream(
                '',
                expected_layout=self.expected_layout,
                input_sample_rate=self.input_sample_rate,
                output_sample_rate=self.output_sample_rate,
                output_frame_size=self.output_frame_size,
                fps=self.fps,
                stream_start_delay=self.stream_start_delay,
                session_policy_config=self.session_policy_config,
            )
            new_stream.weak_factory = weakref.ref(self)
            return new_stream
        except Exception as e:
            logger.opt(exception=True).error(f"Failed to create stream: {e}")
            raise

    async def start_up(self):
        if self.client_session_delegate is not None:
            return

        factory = self
        if self.weak_factory is not None and self.weak_factory() is not None:
            factory = self.weak_factory()

        if factory.client_handler_delegate is None:
            raise RuntimeError("ClientHandlerDelegate is not set.")

        session_id = self.session_id
        if not session_id:
            try:
                session_id = get_current_context().webrtc_id
            except Exception:
                session_id = uuid.uuid4().hex

        selected_encoder, _ = _get_h264_encoder_info()
        logger.debug(f"[{session_id}] H.264 encoder: {selected_encoder}")

        if session_id in factory.streams:
            existing = factory.streams.get(session_id)
            # Cleanup stale entries left by interrupted connections.
            if existing is None or existing.client_session_delegate is None or existing.quit.is_set():
                factory.streams.pop(session_id, None)
            else:
                base_session_id = session_id
                session_id = f"{base_session_id}-{uuid.uuid4().hex[:8]}"
                logger.warning(f"Session id conflict for {base_session_id}, fallback to {session_id}")

        self.session_id = session_id
        if _AV_SYNC_DIAG:
            logger.info(
                f"AV_SYNC_RTC_START session={self.session_id} "
                f"fps={self.fps} input_sr={self.input_sample_rate} "
                f"output_sr={self.output_sample_rate} output_frame_size={self.output_frame_size} "
                f"stream_start_delay={self.stream_start_delay} "
                f"rtp_video_lead_limit_ms={self._av_rtp_video_lead_limit_ms:.1f}"
            )
        existing_delegate = factory.client_handler_delegate.find_session_delegate(session_id)
        if existing_delegate is not None:
            self.client_session_delegate = existing_delegate
            self.owns_session = False
            logger.info(f"Reuse existing session delegate for session {session_id}")
        else:
            try:
                self.client_session_delegate = factory.client_handler_delegate.start_session(
                    session_id=session_id,
                    timestamp_base=self.input_sample_rate,
                )
                self.owns_session = True
            except RuntimeError as e:
                # Another client path (e.g. WS) may create the same session concurrently.
                if "already exists" not in str(e):
                    raise
                existing_delegate = factory.client_handler_delegate.find_session_delegate(session_id)
                if existing_delegate is None:
                    raise
                self.client_session_delegate = existing_delegate
                self.owns_session = False
                logger.info(f"Session {session_id} created concurrently, reusing existing delegate")

        self._apply_pending_device_info()
        factory.streams[session_id] = self

    async def emit(self) -> AudioEmitType:
        try:
            # if not self.args_set.is_set():
            # await self.wait_for_args()
            while self.client_session_delegate is None and not self.quit.is_set():
                await asyncio.sleep(0.01)
            if self.client_session_delegate is None:
                return None

            self._apply_pending_av_sync_reset()
            if not self.first_audio_emitted:
                self.client_session_delegate.clear_data()
                self.first_audio_emitted = True

            while not self.quit.is_set():
                self._apply_pending_av_sync_reset()
                get_data_start = time.perf_counter()
                chat_data = await self.client_session_delegate.get_data(EngineChannelType.AUDIO)
                get_data_wait_ms = (time.perf_counter() - get_data_start) * 1000
                if self._av_sync_reset_requested:
                    self._apply_pending_av_sync_reset()
                    continue
                if chat_data is None or chat_data.data is None:
                    continue
                speech_id = self._chat_data_speech_id(chat_data)
                if self._should_drop_for_av_sync_speech(speech_id):
                    if _AV_SYNC_DIAG:
                        logger.info(
                            f"AV_SYNC_RTC_AUDIO_DROP session={self.session_id} "
                            f"speech_id={speech_id} target_speech_id={self._av_sync_current_speech_id} "
                            f"stream_id={getattr(chat_data, 'stream_id', None)}"
                        )
                    continue
                audio_array = chat_data.data.get_main_data()
                if audio_array is None:
                    continue
                if self._av_sync_reset_requested:
                    self._apply_pending_av_sync_reset()
                    continue
                sample_num = audio_array.shape[-1]
                self.emit_counter.add_property("audio_emit", sample_num / self.output_sample_rate)
                self._av_diag_audio_emit_seq += 1
                self._av_diag_audio_samples_total += sample_num
                if _AV_SYNC_DIAG and self._av_diag_audio_emit_seq % _AV_SYNC_DIAG_EVERY == 0:
                    logger.info(
                        f"AV_SYNC_RTC_AUDIO_EMIT session={self.session_id} "
                        f"mono={time.monotonic():.6f} seq={self._av_diag_audio_emit_seq} "
                        f"samples={sample_num} duration_ms={sample_num / self.output_sample_rate * 1000:.1f} "
                        f"total_audio_ms={self._av_diag_audio_samples_total / self.output_sample_rate * 1000:.1f} "
                        f"wait_ms={get_data_wait_ms:.1f} "
                        f"chat_ts={chat_data.timestamp[0]}/{chat_data.timestamp[1]} "
                        f"is_first={chat_data.is_first_data} is_last={chat_data.is_last_data} "
                        f"stream_id={chat_data.stream_id} speech_id={speech_id}"
                    )
                return self.output_sample_rate, audio_array
        except Exception as e:
            logger.opt(exception=e).error("Error in emit: ")
            raise

    async def video_emit(self) -> VideoEmitType:
        try:
            if not self.first_audio_emitted:
                await asyncio.sleep(0.1)
            while self.client_session_delegate is None and not self.quit.is_set():
                await asyncio.sleep(0.01)
            if self.client_session_delegate is None:
                return None
            
            self._apply_pending_av_sync_reset()
            self._apply_pending_video_catchup()
            self.emit_counter.add_property("video_emit")
            
            while not self.quit.is_set():
                self._apply_pending_av_sync_reset()
                self._apply_pending_video_catchup()

                get_data_start = time.perf_counter()
                video_frame_data: ChatData = await self.client_session_delegate.get_data(EngineChannelType.VIDEO)
                get_data_wait_time = time.perf_counter() - get_data_start
                if self._av_sync_reset_requested:
                    self._apply_pending_av_sync_reset()
                    continue

                _slow_video_threshold_s = 0.12
                if get_data_wait_time > _slow_video_threshold_s:
                    logger.debug(
                        f"[{self.session_id}] Slow video data retrieval: "
                        f"{get_data_wait_time:.3f}s (threshold {_slow_video_threshold_s}s)"
                    )
                
                if video_frame_data is None or video_frame_data.data is None:
                    continue
                speech_id = self._chat_data_speech_id(video_frame_data)
                if self._should_drop_for_av_sync_speech(speech_id):
                    if _AV_SYNC_DIAG:
                        logger.info(
                            f"AV_SYNC_RTC_VIDEO_DROP session={self.session_id} "
                            f"speech_id={speech_id} target_speech_id={self._av_sync_current_speech_id} "
                            f"stream_id={getattr(video_frame_data, 'stream_id', None)}"
                        )
                    continue
                
                frame_data_array = video_frame_data.data.get_main_data()
                if frame_data_array is None:
                    continue
                frame_data = frame_data_array.squeeze()

                self._av_diag_video_emit_seq += 1
                if _AV_SYNC_DIAG and self._av_diag_video_emit_seq % _AV_SYNC_DIAG_EVERY == 0:
                    shape = getattr(frame_data, "shape", None)
                    logger.info(
                        f"AV_SYNC_RTC_VIDEO_EMIT session={self.session_id} "
                        f"mono={time.monotonic():.6f} seq={self._av_diag_video_emit_seq} "
                        f"wait_ms={get_data_wait_time * 1000:.1f} "
                        f"chat_ts={video_frame_data.timestamp[0]}/{video_frame_data.timestamp[1]} "
                        f"is_first={video_frame_data.is_first_data} is_last={video_frame_data.is_last_data} "
                        f"shape={shape} stream_id={video_frame_data.stream_id} speech_id={speech_id}"
                    )
                return frame_data
        except Exception as e:
            logger.opt(exception=e).error("Error in video_emit")
            raise

    async def receive(self, frame: tuple[int, np.ndarray]):
        if self.client_session_delegate is None:
            return
        if self._session_ending:
            return
        timestamp = self.client_session_delegate.get_timestamp()
        if timestamp[0] / timestamp[1] < self.stream_start_delay:
            return
        if self._drop_input_until_persona_active("audio"):
            return
        _, array = frame
        self.client_session_delegate.put_data(
            EngineChannelType.AUDIO,
            array,
            timestamp,
            self.input_sample_rate,
        )

    async def video_receive(self, frame):
        if self.client_session_delegate is None:
            return
        if self._session_ending:
            return
        timestamp = self.client_session_delegate.get_timestamp()
        if timestamp[0] / timestamp[1] < self.stream_start_delay:
            return
        self.client_session_delegate.put_data(
            EngineChannelType.VIDEO,
            frame,
            timestamp,
            self.fps,
        )

    def _persona_active(self) -> bool:
        if self.client_session_delegate is None:
            return False
        shared_states = getattr(self.client_session_delegate, "shared_states", None)
        if shared_states is None:
            return False
        return bool(getattr(shared_states, "persona_runtime", None))

    def _drop_input_until_persona_active(self, data_type: str) -> bool:
        if self._persona_active():
            return False
        if not self._input_gate_drop_logged:
            logger.info(
                f"[{self.session_id}] Input gated until DeviceInfoAck persona_active=true; "
                f"drop={data_type}"
            )
            self._input_gate_drop_logged = True
        return True

    def _send_data_channel_json(self, name: str, request_id: str, payload: Dict[str, Any]):
        if self.chat_channel is None:
            return
        response = {
            "header": {
                "name": name,
                "request_id": request_id,
            },
            "payload": payload,
        }
        try:
            self.chat_channel.send(json.dumps(response, ensure_ascii=False))
        except Exception as e:
            logger.opt(exception=e).warning(f"Failed to send {name} data channel message")

    def _handle_device_info(self, payload: Dict[str, Any], request_id: str):
        if self.client_session_delegate is None:
            return
        if not isinstance(payload, dict):
            payload = {}

        raw_device_sn = payload.get("device_sn")
        device_sn = str(raw_device_sn).strip() if raw_device_sn is not None else ""
        if not device_sn:
            self._send_data_channel_json(
                "Error",
                request_id,
                {
                    "code": "INVALID_DEVICE_INFO",
                    "message": "device_sn is required",
                },
            )
            logger.warning(f"[{self.session_id}] DeviceInfo rejected: device_sn is required")
            return

        def optional_text(value):
            if value is None:
                return None
            cleaned = str(value).strip()
            return cleaned or None

        raw_elder_profile = payload.get("elder_profile")
        elder_profile = None
        if isinstance(raw_elder_profile, dict):
            elder_profile = {
                "nickname": optional_text(raw_elder_profile.get("nickname")),
                "gender": optional_text(raw_elder_profile.get("gender")),
                "age": raw_elder_profile.get("age"),
                "native_place": optional_text(raw_elder_profile.get("native_place")),
            }
            raw_reminders = raw_elder_profile.get("reminders")
            if isinstance(raw_reminders, list):
                reminders = []
                for raw_reminder in raw_reminders[:50]:
                    if not isinstance(raw_reminder, dict):
                        continue
                    try:
                        reminder_id = int(raw_reminder.get("id"))
                        remind_at = int(raw_reminder.get("remind_at"))
                    except (TypeError, ValueError):
                        continue
                    title = optional_text(raw_reminder.get("title"))
                    if reminder_id <= 0 or remind_at <= 0 or not title:
                        continue
                    reminders.append({
                        "id": reminder_id,
                        "title": title[:100],
                        "remind_at": remind_at,
                    })
                elder_profile["reminders"] = reminders

        device_info = {
            "device_sn": device_sn,
            "elder_id": optional_text(payload.get("elder_id")),
            "tenant_id": optional_text(payload.get("tenant_id")),
            "persona_id": optional_text(payload.get("persona_id")),
            "timezone": optional_text(payload.get("timezone")),
            "elder_profile": elder_profile,
            "received_at": time.time(),
        }

        persona_runtime = None
        runtime_enabled = os.getenv("V1_PERSONA_RUNTIME_ENABLED", "1").strip().lower() not in {
            "0", "false", "no", "off"
        }
        if runtime_enabled:
            try:
                persona_runtime = PersonaRuntimeResolver().resolve(
                    persona_id=device_info["persona_id"],
                    elder_id=device_info["elder_id"],
                    tenant_id=device_info["tenant_id"],
                )
            except PersonaRuntimeError as exc:
                if self.client_session_delegate.shared_states is not None:
                    self.client_session_delegate.shared_states.persona_runtime = None
                public_code = "PERSONA_NOT_FOUND" if exc.code == "PERSONA_NOT_OWNED" else exc.code
                public_message = "persona not found" if public_code == "PERSONA_NOT_FOUND" else exc.message
                self._send_data_channel_json(
                    "Error",
                    request_id,
                    {
                        "code": public_code,
                        "message": public_message,
                    },
                )
                logger.warning(
                    f"[{self.session_id}] DeviceInfo persona rejected: "
                    f"code={public_code}, internal_code={exc.code}, message={exc.message}, "
                    f"device_sn={device_info['device_sn']}, elder_id={device_info['elder_id']}, "
                    f"tenant_id={device_info['tenant_id']}, persona_id={device_info['persona_id']}"
                )
                return

        setattr(self.client_session_delegate, "device_info", device_info)
        if self.client_session_delegate.shared_states is not None:
            self.client_session_delegate.shared_states.persona_runtime = persona_runtime
            self.client_session_delegate.shared_states.device_info = device_info
        if persona_runtime:
            self._input_gate_drop_logged = False
        audit_event(
            self.client_session_delegate,
            "device_info_registered",
            device_info=device_info,
            persona_runtime=persona_runtime,
            success=True,
        )
        logger.info(
            f"[{self.session_id}] DeviceInfo registered: "
            f"device_sn={device_info['device_sn']}, "
            f"elder_id={device_info['elder_id']}, "
            f"tenant_id={device_info['tenant_id']}, "
            f"persona_id={device_info['persona_id']}, "
            f"runtime_persona={persona_runtime.get('persona_id') if persona_runtime else None}"
        )
        self._send_data_channel_json(
            "DeviceInfoAck",
            request_id,
            {
                "ok": True,
                "persona_active": bool(persona_runtime),
                "persona_id": persona_runtime.get("persona_id") if persona_runtime else None,
            },
        )

    def _apply_pending_device_info(self) -> None:
        if self.client_session_delegate is None or self._pending_device_info is None:
            return
        payload, request_id = self._pending_device_info
        self._pending_device_info = None
        logger.info(f"[{self.session_id}] Processing DeviceInfo received before session startup")
        self._handle_device_info(payload, request_id)


    def _handle_music_status(self, payload: Dict[str, Any], request_id: str):
        if self.client_session_delegate is None:
            return
        if not isinstance(payload, dict):
            payload = {}

        raw_state = payload.get("state")
        state = str(raw_state or "").strip().lower()
        if state not in _MUSIC_STATUS_ALLOWED_STATES:
            logger.warning(f"[{self.session_id}] MusicStatus rejected: invalid state={raw_state!r}")
            return

        status = {
            "state": state,
            "received_at": time.time(),
            "request_id": request_id,
        }
        for key in ("reason", "title", "artist", "url", "position_ms", "duration_ms", "error"):
            value = payload.get(key)
            if value is not None:
                status[key] = value

        active = state in _MUSIC_STATUS_ACTIVE_STATES
        setattr(self.client_session_delegate, "music_status", status)
        setattr(self.client_session_delegate, "music_player_active", active)
        shared_states = getattr(self.client_session_delegate, "shared_states", None)
        if shared_states is not None:
            shared_states.music_status = status
            shared_states.music_player_active = active

        logger.info(
            f"[{self.session_id}] MusicStatus received: state={state} active={active} "
            f"reason={status.get('reason') or '-'} title={status.get('title') or '-'}"
        )

    def set_channel(self, channel):
            super().set_channel(channel)
            self.chat_channel = channel
            try:
                self.chat_channel_loop = asyncio.get_running_loop()
            except RuntimeError:
                self.chat_channel_loop = None
                
            @channel.on("message")
            def _(message):
                logger.info(f"Received message Custom: {message}")
                try:
                    message = json.loads(message)
                except Exception as e:
                    logger.info(e)
                    message = {}

                if not isinstance(message, dict):
                    message = {}
                header = message.get("header") if isinstance(message.get("header"), dict) else {}
                payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
                message_name = header.get("name")
                request_id = header.get("request_id") or str(uuid.uuid4())

                if message_name == "DeviceInfo":
                    logger.info(f'on_chat_datachannel: {message}')
                    if self.client_session_delegate is None:
                        self._pending_device_info = (dict(payload), request_id)
                        logger.info(
                            f"[{self.session_id}] DeviceInfo received before session startup; cached"
                        )
                        return
                    self._handle_device_info(payload, request_id)
                    return
                if self.client_session_delegate is None:
                    return
                if message_name == "MusicStatus":
                    logger.info(f'on_chat_datachannel: {message}')
                    self._handle_music_status(payload, request_id)
                    return
                if message_name == "ClientEvent":
                    logger.info(f"on_chat_datachannel: ClientEvent type={payload.get('type')}")
                    if getattr(self.client_session_delegate, "device_info", None) is None:
                        self._send_data_channel_json(
                            "Error", request_id,
                            {"code": "DEVICE_INFO_REQUIRED", "message": "DeviceInfo is required before ClientEvent"},
                        )
                        return
                    try:
                        client_event = ClientEventPayload.model_validate(payload).model_dump()
                    except ValueError:
                        self._send_data_channel_json(
                            "Error", request_id,
                            {"code": "INVALID_CLIENT_EVENT", "message": "invalid ClientEvent payload"},
                        )
                        return
                    self._handle_client_event(client_event, request_id)
                    return
                timestamp = self.client_session_delegate.get_timestamp()
                if timestamp[0] / timestamp[1] < self.stream_start_delay:
                    return
                logger.info(f'on_chat_datachannel: {message}')
    
                if message_name == 'Interrupt':
                    self.client_session_delegate.emit_signal(
                        ChatSignal(
                            type=ChatSignalType.INTERRUPT,
                            source_type=ChatSignalSourceType.CLIENT,
                            source_name="rtc",
                        )
                    )
                elif message_name == 'SendHumanText':
                    if self._session_ending:
                        return
                    # self.client_session_delegate.emit_signal(
                    #     ChatSignal(
                    #         type=ChatSignalType.INTERRUPT,
                    #         source_type=ChatSignalSourceType.CLIENT,
                    #         source_name="rtc",
                    #     )
                    # )
                    if self._drop_input_until_persona_active("text"):
                        self._send_data_channel_json(
                            "Error",
                            request_id,
                            {
                                "code": "PERSONA_NOT_ACTIVE",
                                "message": "persona is not active",
                            },
                        )
                        return
                    self.client_session_delegate.emit_signal(
                        ChatSignal(
                            # begin a new round of responding
                            type=ChatSignalType.STREAM_BEGIN,
                            stream_type=ChatDataType.AVATAR_AUDIO,
                            source_type=ChatSignalSourceType.CLIENT,
                            source_name="rtc",
                        )
                    )
                    self.client_session_delegate.put_data(
                        EngineChannelType.TEXT,
                        message['payload']['text'],
                        loopback=True
                    )
                    # Keep immediate user-text echo for pure RTC mode.
                    # WsLam delegate has its own text echo path, so skip here to avoid duplicates.
                    if not hasattr(self.client_session_delegate, "ws_text_queue"):
                        try:
                            payload = message.get("payload", {})
                            response = EchoHumanText(
                                header=MessageHeader(
                                    name=MessageType.ECHO_HUMAN_TEXT,
                                    request_id=str(uuid.uuid4()),
                                ),
                                payload=EchoTextPayload(
                                    stream_key=payload.get("stream_key"),
                                    mode="full_text",
                                    text=payload.get("text", ""),
                                    end_of_speech=payload.get("end_of_speech", True),
                                    metadata=None,
                                ),
                            )
                            self.chat_channel.send(json.dumps(serialize_message(response)))
                        except Exception as e:
                            logger.opt(exception=e).warning("Failed to send local human text echo")
                # else:

                # channel.send(json.dumps({"type": "chat", "unique_id": unique_id, "message": message}))
          
    async def on_chat_datachannel(self, message: Dict, channel):
        # {"type":"chat",id:"标识属于同一段话", "message":"Hello, world!"}
        # unique_id = uuid.uuid4().hex
        pass
    def shutdown(self):
        self.quit.set()
        self._pending_device_info = None
        factory = None
        if self.weak_factory is not None:
            factory = self.weak_factory()
        if factory is None:
            factory = self
        self.client_session_delegate = None
        if self.session_id in factory.streams:
            factory.streams.pop(self.session_id, None)
        if self.owns_session and factory.client_handler_delegate is not None:
            factory.client_handler_delegate.stop_session(self.session_id)
