import asyncio
import json
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
    EchoHumanText,
    EchoTextPayload,
    MessageHeader,
    MessageType,
    serialize_message,
)
from service.v1_adapter.personas.runtime import PersonaRuntimeError, PersonaRuntimeResolver
from engine_utils.conversation_audit_logger import audit_event


_AV_SYNC_DIAG = os.getenv("AV_SYNC_DIAG", "").lower() in {"1", "true", "yes", "on"}
_MUSIC_STATUS_ACTIVE_STATES = {"loading", "playing", "paused"}
_MUSIC_STATUS_ALLOWED_STATES = _MUSIC_STATUS_ACTIVE_STATES | {"stopped", "ended", "error"}
try:
    _AV_SYNC_DIAG_EVERY = max(1, int(os.getenv("AV_SYNC_DIAG_EVERY", "1")))
except ValueError:
    _AV_SYNC_DIAG_EVERY = 1
try:
    _AV_SYNC_VIDEO_LEAD_LIMIT_MS = max(
        0.0,
        float(os.getenv("AV_SYNC_VIDEO_LEAD_LIMIT_MS", "180")),
    )
except ValueError:
    _AV_SYNC_VIDEO_LEAD_LIMIT_MS = 180.0
_AV_SYNC_VIDEO_WARN_LIMIT_MS = 200.0
_AV_SYNC_WAIT_INTERVAL_S = 0.005


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
        self._av_sync_audio_samples_total = 0
        self._av_sync_video_frames_total = 0
        self._av_sync_video_lead_limit_ms = _AV_SYNC_VIDEO_LEAD_LIMIT_MS

    def _av_sync_audio_ms(self) -> float:
        if self.output_sample_rate <= 0:
            return 0.0
        return self._av_sync_audio_samples_total / self.output_sample_rate * 1000

    def _av_sync_video_ms(self, frame_count: Optional[int] = None) -> float:
        if self.fps <= 0:
            return 0.0
        if frame_count is None:
            frame_count = self._av_sync_video_frames_total
        return frame_count / self.fps * 1000

    async def _wait_for_audio_catchup(self, prospective_video_ms: float) -> tuple[float, float, float]:
        wait_start = time.perf_counter()
        audio_ms = self._av_sync_audio_ms()
        drift_ms = prospective_video_ms - audio_ms
        while drift_ms > self._av_sync_video_lead_limit_ms and not self.quit.is_set():
            await asyncio.sleep(_AV_SYNC_WAIT_INTERVAL_S)
            audio_ms = self._av_sync_audio_ms()
            drift_ms = prospective_video_ms - audio_ms
        sync_wait_ms = (time.perf_counter() - wait_start) * 1000
        return audio_ms, drift_ms, sync_wait_ms


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
                f"video_lead_limit_ms={self._av_sync_video_lead_limit_ms:.1f}"
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

        factory.streams[session_id] = self

    async def emit(self) -> AudioEmitType:
        try:
            # if not self.args_set.is_set():
            # await self.wait_for_args()
            while self.client_session_delegate is None and not self.quit.is_set():
                await asyncio.sleep(0.01)
            if self.client_session_delegate is None:
                return None

            if not self.first_audio_emitted:
                self.client_session_delegate.clear_data()
                self.first_audio_emitted = True

            while not self.quit.is_set():
                get_data_start = time.perf_counter()
                chat_data = await self.client_session_delegate.get_data(EngineChannelType.AUDIO)
                get_data_wait_ms = (time.perf_counter() - get_data_start) * 1000
                if chat_data is None or chat_data.data is None:
                    continue
                audio_array = chat_data.data.get_main_data()
                if audio_array is None:
                    continue
                sample_num = audio_array.shape[-1]
                self.emit_counter.add_property("audio_emit", sample_num / self.output_sample_rate)
                self._av_diag_audio_emit_seq += 1
                self._av_diag_audio_samples_total += sample_num
                self._av_sync_audio_samples_total += sample_num
                if _AV_SYNC_DIAG and self._av_diag_audio_emit_seq % _AV_SYNC_DIAG_EVERY == 0:
                    logger.info(
                        f"AV_SYNC_RTC_AUDIO_EMIT session={self.session_id} "
                        f"mono={time.monotonic():.6f} seq={self._av_diag_audio_emit_seq} "
                        f"samples={sample_num} duration_ms={sample_num / self.output_sample_rate * 1000:.1f} "
                        f"total_audio_ms={self._av_diag_audio_samples_total / self.output_sample_rate * 1000:.1f} "
                        f"wait_ms={get_data_wait_ms:.1f} "
                        f"chat_ts={chat_data.timestamp[0]}/{chat_data.timestamp[1]} "
                        f"is_first={chat_data.is_first_data} is_last={chat_data.is_last_data} "
                        f"stream_id={chat_data.stream_id}"
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
            
            self.emit_counter.add_property("video_emit")
            
            while not self.quit.is_set():
                get_data_start = time.perf_counter()
                video_frame_data: ChatData = await self.client_session_delegate.get_data(EngineChannelType.VIDEO)
                get_data_wait_time = time.perf_counter() - get_data_start

                _slow_video_threshold_s = 0.12
                if get_data_wait_time > _slow_video_threshold_s:
                    logger.debug(
                        f"[{self.session_id}] Slow video data retrieval: "
                        f"{get_data_wait_time:.3f}s (threshold {_slow_video_threshold_s}s)"
                    )
                
                if video_frame_data is None or video_frame_data.data is None:
                    continue
                
                frame_data = video_frame_data.data.get_main_data().squeeze()
                if frame_data is None:
                    continue

                prospective_video_frames = self._av_sync_video_frames_total + 1
                prospective_video_ms = self._av_sync_video_ms(prospective_video_frames)
                audio_ms, drift_ms, sync_wait_ms = await self._wait_for_audio_catchup(prospective_video_ms)
                if self.quit.is_set():
                    return None

                self._av_sync_video_frames_total = prospective_video_frames
                self._av_diag_video_emit_seq += 1
                video_ms = self._av_sync_video_ms()
                drift_ms = video_ms - audio_ms
                if drift_ms > _AV_SYNC_VIDEO_WARN_LIMIT_MS:
                    logger.warning(
                        f"AV_SYNC_RTC_VIDEO_LEAD_EXCEEDED session={self.session_id} "
                        f"video_ms={video_ms:.1f} audio_ms={audio_ms:.1f} "
                        f"drift_ms={drift_ms:.1f} limit_ms={self._av_sync_video_lead_limit_ms:.1f} "
                        f"warn_limit_ms={_AV_SYNC_VIDEO_WARN_LIMIT_MS:.1f} "
                        f"sync_wait_ms={sync_wait_ms:.1f}"
                    )
                if _AV_SYNC_DIAG and self._av_diag_video_emit_seq % _AV_SYNC_DIAG_EVERY == 0:
                    shape = getattr(frame_data, "shape", None)
                    logger.info(
                        f"AV_SYNC_RTC_VIDEO_EMIT session={self.session_id} "
                        f"mono={time.monotonic():.6f} seq={self._av_diag_video_emit_seq} "
                        f"total_video_ms={video_ms:.1f} audio_total_ms={audio_ms:.1f} "
                        f"av_drift_ms={drift_ms:.1f} sync_wait_ms={sync_wait_ms:.1f} "
                        f"wait_ms={get_data_wait_time * 1000:.1f} "
                        f"chat_ts={video_frame_data.timestamp[0]}/{video_frame_data.timestamp[1]} "
                        f"is_first={video_frame_data.is_first_data} is_last={video_frame_data.is_last_data} "
                        f"shape={shape} stream_id={video_frame_data.stream_id}"
                    )
                return frame_data
        except Exception as e:
            logger.opt(exception=e).error("Error in video_emit")
            raise

    async def receive(self, frame: tuple[int, np.ndarray]):
        if self.client_session_delegate is None:
            return
        timestamp = self.client_session_delegate.get_timestamp()
        if timestamp[0] / timestamp[1] < self.stream_start_delay:
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
        timestamp = self.client_session_delegate.get_timestamp()
        if timestamp[0] / timestamp[1] < self.stream_start_delay:
            return
        self.client_session_delegate.put_data(
            EngineChannelType.VIDEO,
            frame,
            timestamp,
            self.fps,
        )

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

        device_info = {
            "device_sn": device_sn,
            "elder_id": optional_text(payload.get("elder_id")),
            "tenant_id": optional_text(payload.get("tenant_id")),
            "persona_id": optional_text(payload.get("persona_id")),
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
                self._send_data_channel_json(
                    "Error",
                    request_id,
                    {
                        "code": exc.code,
                        "message": exc.message,
                    },
                )
                logger.warning(
                    f"[{self.session_id}] DeviceInfo persona rejected: "
                    f"code={exc.code}, message={exc.message}, "
                    f"device_sn={device_info['device_sn']}, elder_id={device_info['elder_id']}, "
                    f"tenant_id={device_info['tenant_id']}, persona_id={device_info['persona_id']}"
                )
                return

        setattr(self.client_session_delegate, "device_info", device_info)
        if self.client_session_delegate.shared_states is not None:
            self.client_session_delegate.shared_states.persona_runtime = persona_runtime
            self.client_session_delegate.shared_states.device_info = device_info
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

                if self.client_session_delegate is None:
                    return
                if message_name == "DeviceInfo":
                    logger.info(f'on_chat_datachannel: {message}')
                    self._handle_device_info(payload, request_id)
                    return
                if message_name == "MusicStatus":
                    logger.info(f'on_chat_datachannel: {message}')
                    self._handle_music_status(payload, request_id)
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
                    # self.client_session_delegate.emit_signal(
                    #     ChatSignal(
                    #         type=ChatSignalType.INTERRUPT,
                    #         source_type=ChatSignalSourceType.CLIENT,
                    #         source_name="rtc",
                    #     )
                    # )
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
