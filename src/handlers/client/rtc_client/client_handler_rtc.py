import asyncio
import json
import os
import time
from typing import Dict, Optional, cast, Union, Tuple
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger

from chat_engine.contexts.session_clock import SessionClock
import gradio
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

# ============================================================================
# H.264 Hardware Encoder Configuration (must execute before importing fastrtc)
# ============================================================================
from aiortc.codecs import CODECS, h264
from aiortc import codecs as aiortc_codecs
import av
import fractions

_selected_h264_encoder = 'libx264'
_actual_h264_encoder = None
_AVError = getattr(av, "AVError", av.error.FFmpegError)
_AVCodecError = getattr(av.error, "FFmpegError", Exception)
_AV_SYNC_DIAG = os.getenv("AV_SYNC_DIAG", "").lower() in {"1", "true", "yes", "on"}
try:
    _AV_SYNC_DIAG_EVERY = max(1, int(os.getenv("AV_SYNC_DIAG_EVERY", "1")))
except ValueError:
    _AV_SYNC_DIAG_EVERY = 1


def _prioritize_h264():
    """Prioritize H.264 codec over VP8 in aiortc codec list"""
    video_codecs = CODECS["video"]
    h264_codecs = [c for c in video_codecs if "H264" in c.mimeType]
    other_codecs = [c for c in video_codecs if "H264" not in c.mimeType]
    CODECS["video"] = h264_codecs + other_codecs
    logger.info(f"Video codec priority: {[c.mimeType for c in CODECS['video'][:3]]}")


def _configure_h264_hardware_encoding():
    """Configure H.264 to use hardware encoder (must execute before importing fastrtc)"""
    global _selected_h264_encoder, _actual_h264_encoder

    h264.DEFAULT_BITRATE = 1500000  # 1.5 Mbps default
    h264.MIN_BITRATE = 500000       # 0.5 Mbps minimum
    h264.MAX_BITRATE = 2500000      # 2.5 Mbps maximum

    hardware_encoders = ['h264_nvenc', 'h264_qsv', 'h264_videotoolbox']
    encoder_options = {
        'h264_nvenc': {"preset": "p4", "tune": "ll", "profile": "main", "rc": "cbr", "zerolatency": "1"},
        'h264_qsv': {"preset": "veryfast", "profile": "main"},
        'h264_videotoolbox': {"realtime": "1", "profile": "baseline"},
    }
    for encoder in hardware_encoders:
        try:
            test_codec = av.CodecContext.create(encoder, "w")
            test_codec.width = 640
            test_codec.height = 480
            test_codec.pix_fmt = "yuv420p"
            test_codec.framerate = fractions.Fraction(30, 1)
            test_codec.time_base = fractions.Fraction(1, 30)
            if encoder in encoder_options:
                test_codec.options = encoder_options[encoder]
            test_codec.open()
            test_codec.close()
            _selected_h264_encoder = encoder
            logger.info(f"Detected H.264 hardware encoder: {encoder}")
            break
        except Exception as e:
            logger.debug(f"Hardware encoder {encoder} not available: {e}")

    if _selected_h264_encoder == 'libx264':
        logger.warning("No hardware encoder available, will use libx264 (CPU encoding)")

    def patched_encode_frame(self, frame, force_keyframe):
        global _selected_h264_encoder, _actual_h264_encoder
        if _AV_SYNC_DIAG:
            diag_seq = getattr(self, "_av_sync_encode_seq", 0) + 1
            self._av_sync_encode_seq = diag_seq
        else:
            diag_seq = 0
        if self.codec and (
            frame.width != self.codec.width or frame.height != self.codec.height
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate > 0.1
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
        else:
            frame.pict_type = av.video.frame.PictureType.NONE

        encoder_to_use = getattr(self, "_preferred_encoder", _selected_h264_encoder)
        fallback_attempted = False
        data_to_send = b""

        while True:
            if self.codec is None:
                codec_created = False

                try:
                    self.codec = av.CodecContext.create(encoder_to_use, "w")
                    self.codec.width = frame.width
                    self.codec.height = frame.height
                    self.codec.bit_rate = self.target_bitrate
                    self.codec.pix_fmt = "yuv420p"
                    self.codec.framerate = fractions.Fraction(h264.MAX_FRAME_RATE, 1)
                    self.codec.time_base = fractions.Fraction(1, h264.MAX_FRAME_RATE)

                    if encoder_to_use == 'libx264':
                        self.codec.options = {"level": "31", "tune": "zerolatency", "profile": "baseline", "preset": "ultrafast"}
                    elif encoder_to_use == 'h264_nvenc':
                        self.codec.options = {"preset": "p4", "tune": "ll", "profile": "main", "rc": "cbr", "zerolatency": "1"}
                    elif encoder_to_use == 'h264_qsv':
                        self.codec.options = {"preset": "veryfast", "profile": "main"}
                    elif encoder_to_use == 'h264_videotoolbox':
                        self.codec.options = {"realtime": "1", "profile": "baseline"}

                    codec_created = True
                    logger.info(f"H.264 encoder created: {encoder_to_use}")
                    if _AV_SYNC_DIAG:
                        logger.info(
                            f"AV_SYNC_H264_CODEC_CREATED encoder={encoder_to_use} "
                            f"width={self.codec.width} height={self.codec.height} "
                            f"codec_time_base={self.codec.time_base} codec_framerate={self.codec.framerate} "
                            f"target_bitrate={self.target_bitrate}"
                        )

                except Exception as e:
                    logger.warning(f"Failed to create {encoder_to_use} encoder: {e}")

                    if encoder_to_use != 'libx264':
                        logger.info("Falling back to libx264 software encoder")
                        try:
                            self.codec = av.CodecContext.create("libx264", "w")
                            self.codec.width = frame.width
                            self.codec.height = frame.height
                            self.codec.bit_rate = self.target_bitrate
                            self.codec.pix_fmt = "yuv420p"
                            self.codec.framerate = fractions.Fraction(h264.MAX_FRAME_RATE, 1)
                            self.codec.time_base = fractions.Fraction(1, h264.MAX_FRAME_RATE)
                            self.codec.options = {"level": "31", "tune": "zerolatency", "profile": "baseline"}

                            encoder_to_use = "libx264"
                            codec_created = True
                            logger.info("H.264 encoder created: libx264 (fallback)")
                            if _AV_SYNC_DIAG:
                                logger.info(
                                    f"AV_SYNC_H264_CODEC_CREATED encoder=libx264 fallback=True "
                                    f"width={self.codec.width} height={self.codec.height} "
                                    f"codec_time_base={self.codec.time_base} codec_framerate={self.codec.framerate} "
                                    f"target_bitrate={self.target_bitrate}"
                                )

                        except Exception as fallback_error:
                            logger.error(f"Failed to create fallback encoder: {fallback_error}")
                            raise
                    else:
                        logger.error(f"Failed to create libx264 encoder: {e}")
                        raise

                if codec_created:
                    actual_encoder = self.codec.name
                    _actual_h264_encoder = actual_encoder
                    self._preferred_encoder = encoder_to_use

            try:
                data_to_send = b""
                for package in self.codec.encode(frame):
                    data_to_send += bytes(package)
                break
            except (_AVError, _AVCodecError) as encode_error:
                if fallback_attempted or encoder_to_use == 'libx264':
                    logger.error(f"H.264 encode failed using {encoder_to_use}: {encode_error}")
                    raise

                fallback_attempted = True
                logger.warning(
                    f"H.264 encode failed using {encoder_to_use}, switching to libx264: {encode_error}"
                )
                if self.codec is not None:
                    try:
                        self.codec.close()
                    except Exception as close_error:
                        logger.debug(f"Error closing codec during fallback: {close_error}")
                self.codec = None
                self.buffer_data = b""
                self.buffer_pts = None
                encoder_to_use = 'libx264'
                self._preferred_encoder = encoder_to_use
                _selected_h264_encoder = 'libx264'
                force_keyframe = True
                frame.pict_type = av.video.frame.PictureType.I
                continue

        if data_to_send:
            if _AV_SYNC_DIAG:
                payloads = list(self._split_bitstream(data_to_send))
                if diag_seq % _AV_SYNC_DIAG_EVERY == 0:
                    frame_pts = getattr(frame, "pts", None)
                    frame_time_base = getattr(frame, "time_base", None)
                    if frame_pts is not None and frame_time_base is not None:
                        frame_ms = float(frame_pts * frame_time_base) * 1000
                        rtp_ts = int(float(frame_pts * frame_time_base) * 90000)
                    else:
                        frame_ms = -1.0
                        rtp_ts = -1
                    logger.info(
                        f"AV_SYNC_H264_ENCODE mono={time.monotonic():.6f} seq={diag_seq} "
                        f"frame_pts={frame_pts} frame_time_base={frame_time_base} "
                        f"frame_ms={frame_ms:.1f} rtp_ts_est={rtp_ts} "
                        f"width={frame.width} height={frame.height} "
                        f"bytes={len(data_to_send)} payloads={len(payloads)} "
                        f"force_keyframe={force_keyframe} encoder={encoder_to_use}"
                    )
                yield from payloads
            else:
                yield from self._split_bitstream(data_to_send)

    original_get_encoder = aiortc_codecs.get_encoder

    def patched_get_encoder(codec):
        encoder = original_get_encoder(codec)
        logger.debug(f"get_encoder({codec.mimeType}) -> {type(encoder).__name__}")
        return encoder

    from aiortc import RTCPeerConnection
    original_set_remote = RTCPeerConnection.setRemoteDescription

    async def patched_set_remote_description(self, sessionDescription):
        """Re-order transceiver codecs after SDP negotiation to prioritize H.264"""
        logger.debug(f"setRemoteDescription called with {sessionDescription.type}")

        await original_set_remote(self, sessionDescription)

        from aiortc.codecs import get_capabilities
        from aiortc.rtcpeerconnection import filter_preferred_codecs

        for transceiver in self._RTCPeerConnection__transceivers:
            if transceiver.kind == "video":
                logger.debug(f"Transceiver codecs before: {[c.mimeType for c in transceiver._codecs[:3]]}")

                capabilities = get_capabilities("video")
                current_codecs = transceiver._codecs

                refiltered = filter_preferred_codecs(current_codecs, capabilities.codecs)
                transceiver._codecs = refiltered

                logger.info(f"Video codecs negotiated: {[c.mimeType for c in transceiver._codecs[:2]]}")

    h264.H264Encoder._encode_frame = patched_encode_frame
    aiortc_codecs.get_encoder = patched_get_encoder
    RTCPeerConnection.setRemoteDescription = patched_set_remote_description
    logger.info("H.264 encoder configuration completed")


_prioritize_h264()
_configure_h264_hardware_encoding()

# Import fastrtc after H.264 configuration
# noinspection PyPackageRequirements
from fastrtc import Stream  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from chat_engine.common.client_handler_base import ClientHandlerBase, ClientSessionDelegate
from chat_engine.data_models.engine_channel_type import EngineChannelType
from chat_engine.common.handler_base import HandlerDataInfo, HandlerDetail, HandlerBaseInfo
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_stream_config import ChatStreamConfig
from chat_engine.data_models.chat_engine_config_data import HandlerBaseConfigModel, ChatEngineConfigModel
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.runtime_data.data_bundle import DataBundleDefinition, DataBundleEntry, VariableSize, \
    DataBundle
from handlers.client.ws_client.ws_message_protocol import (
    ChatSignalMessage,
    ChatSignalPayload,
    EchoAvatarText,
    EchoHumanText,
    EchoTextPayload,
    MessageHeader,
    MessageType,
    serialize_message,
)
from service.frontend_service import register_frontend
from service.frontend_service.avatar_image_upload import (
    AvatarImageUploadError,
    save_avatar_image_bytes,
)
from service.frontend_service.voice_clone_upload import (
    VoiceCloneUploadError,
    create_cosyvoice_voice_clone,
    find_voice_clone_audio_file,
    is_voice_enrollment_download_error,
    save_voice_clone_audio_bytes,
)
from service.rtc_service.rtc_provider import RTCProvider
from service.rtc_service.rtc_stream import RtcStream
from chat_engine.data_models.chat_signal_type import ChatSignalType


FLASHHEAD_AVATAR_UPLOAD_ROUTE = "/openavatarchat/avatar/flashhead/image"
VOICE_CLONE_UPLOAD_ROUTE = "/openavatarchat/voice-clone"
VOICE_CLONE_RESET_ROUTE = "/openavatarchat/voice-clone/reset"
VOICE_CLONE_AUDIO_ROUTE = "/openavatarchat/voice-clone/audio/{filename}"
VOICE_CLONE_SAMPLE_TEXT = "今天天气真不错，我想去成都喝茶聊天，慢慢说话。"


class RtcClientSessionDelegate(ClientSessionDelegate):
    def __init__(self):
        self.clock: Optional[SessionClock] = None
        self.data_submitter = None
        self.signal_emitter = None
        self.shared_states = None
        self.output_queues = {
            EngineChannelType.AUDIO: asyncio.Queue(),
            EngineChannelType.VIDEO: asyncio.Queue(),
            EngineChannelType.TEXT: asyncio.Queue(),
        }
        self.input_data_definitions: Dict[EngineChannelType, DataBundleDefinition] = {}
        self.modality_mapping = {
            EngineChannelType.AUDIO: ChatDataType.MIC_AUDIO,
            EngineChannelType.VIDEO: ChatDataType.CAMERA_VIDEO,
            EngineChannelType.TEXT: ChatDataType.HUMAN_TEXT,
        }

    async def get_data(self, modality: EngineChannelType, timeout: Optional[float] = 0.1) -> Optional[ChatData]:
        data_queue = self.output_queues.get(modality)
        if data_queue is None:
            return None
        if timeout is not None and timeout > 0:
            try:
                data = await asyncio.wait_for(data_queue.get(), timeout)
            except asyncio.TimeoutError:
                return None
        else:
            data = await data_queue.get()
        return data

    def put_data(self, modality: EngineChannelType, data: Union[np.ndarray, str],
                 timestamp: Optional[Tuple[int, int]] = None, samplerate: Optional[int] = None, loopback: bool = False):
        if timestamp is None:
            timestamp = self.get_timestamp()
        if self.data_submitter is None:
            return
        definition = self.input_data_definitions.get(modality)
        chat_data_type = self.modality_mapping.get(modality)
        if chat_data_type is None or definition is None:
            return
        data_bundle = DataBundle(definition)
        is_last_data = False
        if modality == EngineChannelType.AUDIO:
            data_bundle.set_main_data(data.squeeze()[np.newaxis, ...])
        elif modality == EngineChannelType.VIDEO:
            data_bundle.set_main_data(data[np.newaxis, ...])
        elif modality == EngineChannelType.TEXT:
            data_bundle.set_main_data(data)
            is_last_data = True  # 文本消息立即完成
        else:
            return
        chat_data = ChatData(
            source="client",
            type=chat_data_type,
            data=data_bundle,
            timestamp=timestamp,
        )
        self.data_submitter.submit(chat_data, finish_stream=is_last_data)
        if loopback:
            self.output_queues[modality].put_nowait(chat_data)

    def get_timestamp(self):
        return self.clock.get_timestamp()

    def emit_signal(self, signal: ChatSignal):
        if self.signal_emitter is not None:
            self.signal_emitter.emit(signal)
        else:
            logger.warning("signal_emitter is None, cannot emit signal")

    def clear_data(self):
        for data_queue in self.output_queues.values():
            while not data_queue.empty():
                data_queue.get_nowait()


class ClientRtcConfigModel(HandlerBaseConfigModel, BaseModel):
    connection_ttl: int = Field(default=900)
    turn_config: Optional[Dict] = Field(default=None)
    output_video_fps: int = Field(
        default=30, description="Output video frame rate for RTC stream. Must match the avatar handler's fps (e.g. 20 for MuseTalk, 30 for LiteAvatar) to ensure correct lip-sync PTS.")
    input_video_enabled: bool = Field(
        default=True,
        description="Whether the browser should request a real user camera track. Disable for audio-only testing.")


class ClientRtcContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config: Optional[ClientRtcConfigModel] = None
        self.client_session_delegate: Optional[RtcClientSessionDelegate] = None


class ClientHandlerRtc(ClientHandlerBase):
    def __init__(self):
        super().__init__()
        self.engine_config = None
        self.handler_config = None
        self.rtc_streamer_factory: Optional[RtcStream] = None

        self.output_bundle_definitions: Dict[EngineChannelType, DataBundleDefinition] = {}

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            config_model=ClientRtcConfigModel,
            client_session_delegate_class=RtcClientSessionDelegate,
        )

    def prepare_rtc_definitions(self):
        output_video_fps = self.handler_config.output_video_fps if self.handler_config else 30
        self.rtc_streamer_factory = RtcStream(
            session_id=None,
            expected_layout="mono",
            input_sample_rate=16000,
            output_sample_rate=24000,
            output_frame_size=480,
            fps=output_video_fps,
            stream_start_delay=0.5,
        )
        self.rtc_streamer_factory.client_handler_delegate = self.handler_delegate

        audio_output_definition = DataBundleDefinition()
        audio_output_definition.add_entry(DataBundleEntry.create_audio_entry(
            "mic_audio",
            1,
            16000,
        ))
        audio_output_definition.lockdown()
        self.output_bundle_definitions[EngineChannelType.AUDIO] = audio_output_definition

        video_output_definition = DataBundleDefinition()
        video_output_definition.add_entry(DataBundleEntry.create_framed_entry(
            "camera_video",
            [VariableSize(), VariableSize(), VariableSize(), 3],
            0,
            output_video_fps
        ))
        video_output_definition.lockdown()
        self.output_bundle_definitions[EngineChannelType.VIDEO] = video_output_definition

        text_output_definition = DataBundleDefinition()
        text_output_definition.add_entry(DataBundleEntry.create_text_entry(
            "human_text",
        ))
        text_output_definition.lockdown()
        self.output_bundle_definitions[EngineChannelType.TEXT] = text_output_definition

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[HandlerBaseConfigModel] = None):
        self.engine_config = engine_config
        self.handler_config = cast(ClientRtcConfigModel, handler_config)
        self.prepare_rtc_definitions()

    def build_frontend_init_config(self, avatar_config, rtc_configuration):
        track_constraints = {
            "audio": {
                "sampleRate": 16000,
                "channelCount": 1,
                "autoGainControl": False,
                "noiseSuppression": False,
                "echoCancellation": True,
            }
        }
        if self.handler_config and not self.handler_config.input_video_enabled:
            track_constraints["video"] = False
        else:
            track_constraints["video"] = {}

        config = {
            "avatar_config": avatar_config,
            "rtc_configuration": rtc_configuration,
            "track_constraints": track_constraints,
        }
        if self._find_flashhead_handler() is not None:
            config["avatar_clone"] = {
                "enabled": True,
                "upload_route": FLASHHEAD_AVATAR_UPLOAD_ROUTE,
            }
        voice_clone_handler = self._find_voice_clone_tts_handler()
        if voice_clone_handler is not None:
            status = voice_clone_handler.get_voice_clone_status()
            config["voice_clone"] = {
                "enabled": True,
                "upload_route": VOICE_CLONE_UPLOAD_ROUTE,
                "reset_route": VOICE_CLONE_RESET_ROUTE,
                "sample_text": VOICE_CLONE_SAMPLE_TEXT,
                "active": status.get("active", False),
                "model_name": status.get("model_name"),
            }
        return config

    def _has_active_sessions(self) -> bool:
        if self.handler_delegate.session_delegates:
            return True
        if self.rtc_streamer_factory is None:
            return False
        for stream in self.rtc_streamer_factory.streams.values():
            if stream is not None and stream.client_session_delegate is not None and not stream.quit.is_set():
                return True
        return False

    def _find_flashhead_handler(self):
        engine = self.handler_delegate.engine_ref() if self.handler_delegate.engine_ref else None
        handler_manager = getattr(engine, "handler_manager", None)
        if handler_manager is None:
            return None
        for registry in handler_manager.get_enabled_handler_registries(order_by_priority=False):
            handler = getattr(registry, "handler", None)
            if callable(getattr(handler, "update_condition_image", None)):
                return handler
        return None

    def _find_voice_clone_tts_handler(self):
        engine = self.handler_delegate.engine_ref() if self.handler_delegate.engine_ref else None
        handler_manager = getattr(engine, "handler_manager", None)
        if handler_manager is None:
            return None
        for registry in handler_manager.get_enabled_handler_registries(order_by_priority=False):
            handler = getattr(registry, "handler", None)
            if callable(getattr(handler, "update_voice_clone", None)) and callable(
                getattr(handler, "get_voice_clone_target_model", None)
            ):
                return handler
        return None

    @staticmethod
    def _external_url(request: Request, path: str) -> str:
        configured_public_url = os.getenv("OPENAVATARCHAT_PUBLIC_URL", "").strip().rstrip("/")
        if configured_public_url:
            return f"{configured_public_url}{path}"

        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host")
        scheme = (forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme) or "http"
        host = (forwarded_host.split(",")[0].strip() if forwarded_host else request.headers.get("host")) or request.url.netloc
        origin = request.headers.get("origin", "").strip()
        if origin:
            parsed_origin = urlparse(origin)
            if parsed_origin.scheme and parsed_origin.netloc:
                scheme = parsed_origin.scheme
                host = parsed_origin.netloc
        if scheme == "http" and host.endswith(":8443"):
            scheme = "https"
        return f"{scheme}://{host}{path}"

    def register_flashhead_avatar_upload_route(self, app: FastAPI):
        @app.post(FLASHHEAD_AVATAR_UPLOAD_ROUTE)
        async def upload_flashhead_avatar_image(file: UploadFile = File(...)):
            if self._has_active_sessions():
                raise HTTPException(
                    status_code=409,
                    detail="Please stop the current conversation before cloning a new avatar.",
                )

            flashhead_handler = self._find_flashhead_handler()
            if flashhead_handler is None:
                raise HTTPException(status_code=404, detail="FlashHead avatar handler is not enabled.")

            data = await file.read()
            try:
                upload_result = save_avatar_image_bytes(
                    data=data,
                    original_filename=file.filename,
                    content_type=file.content_type,
                )
                flashhead_handler.update_condition_image(upload_result.absolute_path)
            except AvatarImageUploadError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            except Exception as exc:
                logger.opt(exception=exc).error("Failed to update FlashHead avatar image")
                raise HTTPException(
                    status_code=500,
                    detail="Failed to update FlashHead avatar image.",
                ) from exc

            return upload_result.to_response()

    def register_voice_clone_routes(self, app: FastAPI):
        @app.get(VOICE_CLONE_AUDIO_ROUTE)
        async def get_voice_clone_audio(filename: str):
            try:
                audio_path = find_voice_clone_audio_file(filename)
            except VoiceCloneUploadError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            return FileResponse(audio_path, media_type="audio/wav", filename=filename)

        @app.post(VOICE_CLONE_UPLOAD_ROUTE)
        async def upload_voice_clone_audio(request: Request, file: UploadFile = File(...)):
            if self._has_active_sessions():
                raise HTTPException(
                    status_code=409,
                    detail="Please stop the current conversation before cloning a voice.",
                )

            tts_handler = self._find_voice_clone_tts_handler()
            if tts_handler is None:
                raise HTTPException(status_code=404, detail="CosyVoice TTS handler is not enabled.")

            data = await file.read()
            try:
                upload_result = save_voice_clone_audio_bytes(
                    data=data,
                    original_filename=file.filename,
                    content_type=file.content_type,
                )
                audio_url = self._external_url(
                    request,
                    f"/openavatarchat/voice-clone/audio/{upload_result.filename}",
                )
                target_model = tts_handler.get_voice_clone_target_model()
                logger.info(f"Creating CosyVoice clone with public audio URL: {audio_url}")
                voice_id = await asyncio.to_thread(
                    create_cosyvoice_voice_clone,
                    audio_url=audio_url,
                    target_model=target_model,
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
                tts_handler.update_voice_clone(voice_id, model_name=target_model)
            except VoiceCloneUploadError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            except Exception as exc:
                if is_voice_enrollment_download_error(exc):
                    logger.opt(exception=exc).error("Bailian could not download voice clone audio")
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "Bailian could not download the recorded audio. "
                            "Please check the OpenAvatarChat public URL and try again."
                        ),
                    ) from exc
                logger.opt(exception=exc).error("Failed to clone voice with CosyVoice")
                raise HTTPException(status_code=500, detail="Failed to clone voice.") from exc

            return upload_result.to_response(voice_id=voice_id, model_name=target_model)

        @app.post(VOICE_CLONE_RESET_ROUTE)
        async def reset_voice_clone():
            if self._has_active_sessions():
                raise HTTPException(
                    status_code=409,
                    detail="Please stop the current conversation before resetting the voice.",
                )

            tts_handler = self._find_voice_clone_tts_handler()
            if tts_handler is None:
                raise HTTPException(status_code=404, detail="CosyVoice TTS handler is not enabled.")
            tts_handler.reset_voice_clone()
            return {"status": "ok", **tts_handler.get_voice_clone_status()}

    def setup_rtc_ui(self, ui, parent_block, fastapi: FastAPI, avatar_config):
        turn_entity = RTCProvider().prepare_rtc_configuration(self.handler_config.turn_config)
        if turn_entity is None:
            turn_entity = RTCProvider().prepare_rtc_configuration(self.engine_config.turn_config)

        webrtc = Stream(
            modality="audio-video",
            mode="send-receive",
            time_limit=self.handler_config.connection_ttl,
            rtc_configuration=turn_entity.rtc_configuration if turn_entity is not None else None,
            handler=self.rtc_streamer_factory,
            concurrency_limit=self.handler_config.concurrent_limit,
        )
        webrtc.mount(fastapi)
        self.register_flashhead_avatar_upload_route(fastapi)
        self.register_voice_clone_routes(fastapi)

        def init_config_provider():
            return self.build_frontend_init_config(
                avatar_config, turn_entity.rtc_configuration if turn_entity is not None else None
            )

        register_frontend(
            app=fastapi,
            ui=ui,
            parent_block=parent_block,
            init_config=init_config_provider,
        )

    def on_setup_app(self, app: FastAPI, ui: gradio.blocks.Block, parent_block: Optional[gradio.blocks.Block] = None):
        avatar_config = {}
        self.setup_rtc_ui(ui, parent_block, app, avatar_config)

    def create_context(self, session_context: SessionContext,
                       handler_config: Optional[HandlerBaseConfigModel] = None) -> HandlerContext:
        if not isinstance(handler_config, ClientRtcConfigModel):
            handler_config = ClientRtcConfigModel()
        context = ClientRtcContext(session_context.session_info.session_id)
        context.config = handler_config
        return context

    def start_context(self, session_context: SessionContext, handler_context: HandlerContext):
        pass

    def on_setup_session_delegate(self, session_context: SessionContext, handler_context: HandlerContext,
                                  session_delegate: ClientSessionDelegate):
        handler_context = cast(ClientRtcContext, handler_context)
        session_delegate = cast(RtcClientSessionDelegate, session_delegate)

        session_delegate.clock = session_context.get_clock()
        session_delegate.data_submitter = handler_context.data_submitter
        session_delegate.signal_emitter = handler_context.signal_emitter
        session_delegate.input_data_definitions = self.output_bundle_definitions
        session_delegate.shared_states = session_context.shared_states

        handler_context.client_session_delegate = session_delegate

    def create_handler_detail(self, _session_context, _handler_context):
        inputs = {
            ChatDataType.AVATAR_AUDIO: HandlerDataInfo(
                type=ChatDataType.AVATAR_AUDIO
            ),
            ChatDataType.AVATAR_VIDEO: HandlerDataInfo(
                type=ChatDataType.AVATAR_VIDEO
            ),
            ChatDataType.AVATAR_TEXT: HandlerDataInfo(
                type=ChatDataType.AVATAR_TEXT
            ),
            ChatDataType.HUMAN_TEXT: HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT
            ),
        }
        _no_link = ChatStreamConfig(cancelable=False, auto_link_input=False)
        outputs = {
            ChatDataType.MIC_AUDIO: HandlerDataInfo(
                type=ChatDataType.MIC_AUDIO,
                definition=self.output_bundle_definitions[EngineChannelType.AUDIO],
                output_stream_config=_no_link,
            ),
            ChatDataType.CAMERA_VIDEO: HandlerDataInfo(
                type=ChatDataType.CAMERA_VIDEO,
                definition=self.output_bundle_definitions[EngineChannelType.VIDEO],
                output_stream_config=_no_link,
            ),
            ChatDataType.HUMAN_TEXT: HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT,
                definition=self.output_bundle_definitions[EngineChannelType.TEXT],
                output_stream_config=_no_link,
            ),
        }
        return HandlerDetail(
            inputs=inputs,
            outputs=outputs
        )

    def get_handler_detail(self, session_context: SessionContext, context: HandlerContext) -> HandlerDetail:
        return self.create_handler_detail(session_context, context)

    def _get_chat_channel(self, session_id: str):
        if self.rtc_streamer_factory is None:
            return None
        stream = self.rtc_streamer_factory.streams.get(session_id)
        if stream is None:
            return None
        return stream

    def _send_message_to_chat_channel(self, session_id: str, message) -> bool:
        stream = self._get_chat_channel(session_id)
        if stream is None or stream.chat_channel is None:
            return False
        payload = json.dumps(serialize_message(message))
        loop = getattr(stream, "chat_channel_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(stream.chat_channel.send, payload)
            return True
        try:
            stream.chat_channel.send(payload)
            return True
        except RuntimeError as e:
            logger.warning(f"Failed to send chat channel message for session {session_id}: {e}")
            return False

    def _send_text_to_chat_channel(self, context: ClientRtcContext, inputs: ChatData) -> bool:
        stream_key_str = inputs.stream_id.stream_key_str if inputs.stream_id else None
        text_end = inputs.is_last_data
        text = inputs.data.get_main_data() if inputs.data is not None else ""
        stream_metadata = None
        if inputs.data is not None and getattr(inputs.data, "metadata", None):
            stream_metadata = dict(inputs.data.metadata)
        if inputs.type == ChatDataType.HUMAN_TEXT:
            response = EchoHumanText(
                header=MessageHeader(name=MessageType.ECHO_HUMAN_TEXT, request_id=str(uuid4())),
                payload=EchoTextPayload(
                    stream_key=stream_key_str,
                    mode="full_text",
                    text=text,
                    end_of_speech=text_end,
                    metadata=stream_metadata,
                ),
            )
        elif inputs.type == ChatDataType.AVATAR_TEXT:
            response = EchoAvatarText(
                header=MessageHeader(name=MessageType.ECHO_AVATAR_TEXT, request_id=str(uuid4())),
                payload=EchoTextPayload(
                    stream_key=stream_key_str,
                    mode="increment",
                    text=text,
                    end_of_speech=text_end,
                    metadata=stream_metadata,
                ),
            )
        else:
            return False

        sent = self._send_message_to_chat_channel(context.session_id, response)
        client_action = None
        if isinstance(stream_metadata, dict):
            client_action = stream_metadata.get("client_action")
        if isinstance(client_action, dict):
            logger.info(
                f"RTC chat channel client_action send: session={context.session_id} "
                f"sent={sent} type={client_action.get('type')} data_type={inputs.type.value} "
                f"stream_key={stream_key_str} text_len={len(text or '')} end={text_end}"
            )
        return sent

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        context = cast(ClientRtcContext, context)
        if context.client_session_delegate is None:
            return
        if inputs.type.channel_type == EngineChannelType.TEXT:
            if not self._send_text_to_chat_channel(context, inputs):
                logger.debug(f"Chat channel not ready for session {context.session_id}, skip text forwarding")
            return
        data_queue = context.client_session_delegate.output_queues.get(inputs.type.channel_type)
        if data_queue is not None:
            data_queue.put_nowait(inputs)

    def destroy_context(self, context: HandlerContext):
        pass

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        context = cast(ClientRtcContext, context)
        logger.info(
            f"Received signal: {signal.type} for stream: {signal.related_stream.data_type if signal.related_stream else None}")
        if signal.related_stream is not None and signal.related_stream.data_type == ChatDataType.CLIENT_PLAYBACK:

            signal_payload = ChatSignalPayload(
                type=signal.type,
                source_type=signal.source_type,
                signal_data=signal.signal_data,
            )
            if signal.related_stream is not None:
                signal_payload.stream_type = signal.related_stream.data_type.value
                signal_payload.stream_producer = signal.related_stream.producer_name
                signal_payload.stream_key = signal.related_stream.stream_key_str

            if signal.type == ChatSignalType.STREAM_CANCEL:
                current_stream = context.stream_manager.find_stream(signal.related_stream)
                signal_payload.parent_stream_keys = [
                    stream.stream_key_str for stream in current_stream.ancestor_streams]

            message = ChatSignalMessage(
                header=MessageHeader(name=MessageType.CHAT_SIGNAL, request_id=str(uuid4())),
                payload=signal_payload,
            )
            self._send_message_to_chat_channel(context.session_id, message)
