"""
FlashHead Avatar Handler for OpenAvatarChat.

Integrates SoulX-FlashHead Lite mode as a server-side rendering avatar.
Input: AVATAR_AUDIO (from TTS) -> Output: AVATAR_VIDEO (512x512) + AVATAR_AUDIO (passthrough)

Architecture follows MuseTalk handler pattern:
- Pipeline loaded once in load(), model weights shared
- Per-session FlashHeadProcessor created in create_context()
- CLIENT_PLAYBACK lifecycle stream for duplex interrupt support
"""
import os

# xformers has a strict flash-attn version window check that may reject
# newer ABI-compatible releases.  Bypass it so the two libraries coexist.
os.environ.setdefault("XFORMERS_IGNORE_FLASH_VERSION_CHECK", "1")
import sys
import threading
import copy
import time
from typing import Dict, Optional, cast

import numpy as np
import torch
from loguru import logger

from chat_engine.data_models.chat_data_type import ChatDataType, EngineChannelType
from chat_engine.common.handler_base import (
    HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail, ChatDataConsumeMode,
)
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalType
from chat_engine.data_models.chat_stream_config import ChatStreamConfig
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundleDefinition, DataBundleEntry, DataBundle, VariableSize,
)

from handlers.avatar.flashhead.flashhead_config import FlashHeadConfig
from handlers.avatar.flashhead.flashhead_processor import FlashHeadProcessor, FlashHeadProcessorCallbacks
from engine_utils.conversation_audit_logger import audit_event


class FlashHeadContext(HandlerContext):
    """Per-session context for FlashHead avatar handler."""

    def __init__(self, session_id: str, processor: FlashHeadProcessor):
        super().__init__(session_id)
        self.config: Optional[FlashHeadConfig] = None
        self.processor = processor
        self.output_data_definitions: Dict[ChatDataType, DataBundleDefinition] = {}

        self._current_tts_stream_key: Optional[str] = None
        self._interrupted_tts_stream_keys = set()
        self._stream_key_lock = threading.Lock()
        self._playback_streamer = None
        self.shared_states = None
        self._applied_condition_image_path: Optional[str] = None

    def init_playback_streamer(self):
        """Eagerly create a CLIENT_PLAYBACK lifecycle streamer.
        Must be called after stream_manager is set."""
        if self._playback_streamer is None and self.stream_manager is not None:
            self._playback_streamer = self.stream_manager.create_lifecycle_streamer(
                data_type=ChatDataType.CLIENT_PLAYBACK,
                producer_name="FlashHead",
                config=ChatStreamConfig(cancelable=False),
            )
        return self._playback_streamer

    def get_playback_streamer(self):
        return self._playback_streamer

    def _build_callbacks(self) -> FlashHeadProcessorCallbacks:
        """Build callbacks that bridge Processor output to engine's submit_data."""

        def on_video_frame(frame: np.ndarray, speech_id: Optional[str] = None):
            self._return_data(frame, ChatDataType.AVATAR_VIDEO, speech_id=speech_id)

        def on_audio_frame(audio_data: np.ndarray, speech_id: Optional[str] = None):
            self._return_data(audio_data, ChatDataType.AVATAR_AUDIO, speech_id=speech_id)

        def on_speech_end(speech_id: str):
            streamer = self.get_playback_streamer()
            with self._stream_key_lock:
                current_key = self._current_tts_stream_key
                if current_key is None:
                    logger.debug(
                        f"FlashHead: on_speech_end ignored (no active stream, "
                        f"likely interrupted): speech_id={speech_id}"
                    )
                    return
                if speech_id is not None and speech_id != current_key:
                    audit_event(
                        self,
                        "digital_human_error",
                        stream_key=current_key,
                        avatar="flashhead",
                        speech_id=speech_id,
                        error="speech_id mismatch",
                        success=False,
                    )
                    logger.warning(
                        f"FlashHead: on_speech_end speech_id mismatch, "
                        f"callback={speech_id}, current={current_key}, skip closing"
                    )
                    return
                self._current_tts_stream_key = None
            if streamer is not None:
                streamer.finish_current()
                audit_event(
                    self,
                    "digital_human_success",
                    stream_key=current_key,
                    avatar="flashhead",
                    speech_id=speech_id,
                    success=True,
                )
                logger.info(f"FlashHead: CLIENT_PLAYBACK stream closed for stream_key={current_key}")

        return FlashHeadProcessorCallbacks(
            on_video_frame=on_video_frame,
            on_audio_frame=on_audio_frame,
            on_speech_end=on_speech_end,
        )

    def _return_data(self, data: np.ndarray, chat_data_type: ChatDataType,
                     speech_id: Optional[str] = None) -> None:
        """Package and submit output data for downstream consumption."""
        definition = self.output_data_definitions.get(chat_data_type)
        if definition is None:
            logger.error(f"FlashHead: Definition is None, chat_data_type={chat_data_type}")
            return
        data_bundle = DataBundle(definition)
        if chat_data_type.channel_type == EngineChannelType.AUDIO:
            if data is not None:
                if data.dtype != np.float32:
                    data = data.astype(np.float32)
                if data.ndim == 1:
                    data = data[np.newaxis, ...]
                elif data.ndim == 2 and data.shape[0] != 1:
                    data = data[:1, ...]
            else:
                data = np.zeros([1, 0], dtype=np.float32)
            data_bundle.set_main_data(data)
        elif chat_data_type.channel_type == EngineChannelType.VIDEO:
            data_bundle.set_main_data(data[np.newaxis, ...])
        else:
            return
        if speech_id is not None:
            data_bundle.metadata["flashhead_speech_id"] = speech_id
        chat_data = ChatData(type=chat_data_type, data=data_bundle)
        self.submit_data(chat_data)

    def interrupt(self):
        """Interrupt current speech: stop processor, clear tracking state.

        Called when CLIENT_PLAYBACK stream is cancelled (via STREAM_CANCEL signal).
        The engine's forward_cancel_signal cascade automatically cancels the
        CLIENT_PLAYBACK stream; we only clear tracking state here to prevent
        on_speech_end from doing a duplicate finish.
        """
        logger.info("FlashHead interrupt: notifying processor")
        with self._stream_key_lock:
            if self._current_tts_stream_key:
                self._interrupted_tts_stream_keys.add(self._current_tts_stream_key)
            self._current_tts_stream_key = None
        if self.processor is not None:
            self.processor.interrupt()
        logger.info("FlashHead interrupt: done")

    def clear(self) -> None:
        logger.info("FlashHead: clear context")


class HandlerAvatarFlashHead(HandlerBase):
    """FlashHead avatar handler: audio-driven diffusion-based talking head generation.

    Uses SoulX-FlashHead Lite model for real-time streaming video generation.
    Single pipeline instance shared across sessions; per-session state managed
    by FlashHeadProcessor instances.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pipeline = None
        self.infer_params: Optional[dict] = None
        self.output_data_definitions: Dict[ChatDataType, DataBundleDefinition] = {}
        self._handler_config: Optional[FlashHeadConfig] = None
        self._flashhead_algo_path: Optional[str] = None
        self._condition_image_path: Optional[str] = None
        self._default_condition_image_path: Optional[str] = None
        self._condition_image_lock = threading.RLock()

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            config_model=FlashHeadConfig,
            load_priority=-999,
        )

    def load(self, engine_config: ChatEngineConfigModel,
             handler_config: Optional[FlashHeadConfig] = None):
        if not isinstance(handler_config, FlashHeadConfig):
            handler_config = FlashHeadConfig()
        self._handler_config = handler_config

        # --- Setup output data definitions ---
        audio_output_definition = DataBundleDefinition()
        audio_output_definition.add_entry(DataBundleEntry.create_audio_entry(
            "avatar_flashhead_audio", 1, handler_config.output_audio_sample_rate,
        ))
        audio_output_definition.lockdown()
        self.output_data_definitions[ChatDataType.AVATAR_AUDIO] = audio_output_definition

        video_output_definition = DataBundleDefinition()
        video_output_definition.add_entry(DataBundleEntry.create_framed_entry(
            "avatar_flashhead_video",
            [VariableSize(), VariableSize(), VariableSize(), 3],
            0, handler_config.fps,
        ))
        video_output_definition.lockdown()
        self.output_data_definitions[ChatDataType.AVATAR_VIDEO] = video_output_definition

        # --- Workaround: cuDNN bundled with torch may not support Blackwell GPUs.
        #     Disable it to fall back to native CUDA kernels. ---
        if not torch.backends.cudnn.enabled:
            logger.info("FlashHead: cuDNN already disabled")
        else:
            try:
                x = torch.randn(1, 1, 2, 2, device="cuda")
                torch.nn.functional.conv2d(x, torch.randn(1, 1, 1, 1, device="cuda"))
            except RuntimeError:
                torch.backends.cudnn.enabled = False
                logger.warning("FlashHead: cuDNN not functional on this GPU, disabled")

        # --- Add SoulX-FlashHead to Python path ---
        flashhead_algo_path = os.path.join(self.handler_root, "SoulX-FlashHead")
        self._flashhead_algo_path = flashhead_algo_path
        if flashhead_algo_path not in sys.path:
            sys.path.insert(0, flashhead_algo_path)
            logger.info(f"Added FlashHead algo path to sys.path: {flashhead_algo_path}")

        # --- Resolve all paths to absolute BEFORE changing CWD ---
        project_root = os.getcwd()
        ckpt_dir = os.path.join(project_root, handler_config.ckpt_dir)
        wav2vec_dir = os.path.join(project_root, handler_config.wav2vec_dir)
        cond_image_path = handler_config.cond_image_path
        if not os.path.isabs(cond_image_path):
            cond_image_path = os.path.join(project_root, cond_image_path)

        # --- Pre-crop face at handler level so the submodule receives a
        #     ready-to-use portrait and we avoid its broken mediapipe dep. ---
        if handler_config.use_face_crop:
            from handlers.avatar.flashhead.flashhead_face_crop import crop_face
            try:
                cropped = crop_face(cond_image_path)
                cropped_path = cond_image_path + ".cropped.png"
                cropped.save(cropped_path)
                logger.info(f"FlashHead: face-cropped image saved to {cropped_path}")
                cond_image_path = cropped_path
            except Exception as e:
                logger.warning(f"FlashHead: face crop failed ({e}), using original image")

        # --- SoulX-FlashHead uses relative paths (e.g. flash_head/configs/infer_params.yaml)
        #     at module-level, so we temporarily switch CWD during import & init. ---
        original_cwd = os.getcwd()
        os.chdir(flashhead_algo_path)
        try:
            # --- Initialize FlashHead pipeline ---
            from flash_head.inference import get_pipeline, get_base_data, get_infer_params

            # Disable torch.compile for VAE/model: the inductor backend may
            # generate incorrect strides on newer GPU architectures (Blackwell).
            import flash_head.src.pipeline.flash_head_pipeline as _fh_pipeline
            _fh_pipeline.COMPILE_MODEL = False
            _fh_pipeline.COMPILE_VAE = False
            logger.info("FlashHead: Disabled torch.compile for model & VAE")

            logger.info(f"Loading FlashHead pipeline: ckpt_dir={ckpt_dir}, "
                         f"model_type={handler_config.model_type}, wav2vec_dir={wav2vec_dir}, "
                         f"use_face_crop={handler_config.use_face_crop}, "
                         f"cond_image={cond_image_path}")

            self.pipeline = get_pipeline(
                world_size=1,
                ckpt_dir=ckpt_dir,
                model_type=handler_config.model_type,
                wav2vec_dir=wav2vec_dir,
            )

            # Face cropping already handled above; always pass False to submodule
            get_base_data(
                self.pipeline,
                cond_image_path_or_dir=cond_image_path,
                base_seed=handler_config.base_seed,
                use_face_crop=False,
            )
            self._condition_image_path = cond_image_path
            self._default_condition_image_path = cond_image_path

            # Suppress per-step print() noise from generate() —
            # the pipeline gates those logs behind `if self.rank == 0`.
            self.pipeline.rank = 1

            self.infer_params = get_infer_params()
            logger.info(
                f"FlashHead pipeline loaded: frame_num={self.infer_params['frame_num']}, "
                f"motion_frames_num={self.infer_params['motion_frames_num']}, "
                f"slice_len={self.infer_params['frame_num'] - self.infer_params['motion_frames_num']}"
            )
        except Exception as e:
            logger.error(f"FlashHead pipeline initialization failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise
        finally:
            os.chdir(original_cwd)

    def _resolve_condition_image_path(self, cond_image_path: str) -> str:
        project_root = os.getcwd()
        if not os.path.isabs(cond_image_path):
            cond_image_path = os.path.join(project_root, cond_image_path)
        if not os.path.exists(cond_image_path):
            raise FileNotFoundError(f"FlashHead condition image does not exist: {cond_image_path}")
        return cond_image_path

    def _prepare_condition_image(self, cond_image_path: str, handler_config: FlashHeadConfig) -> str:
        cond_image_path = self._resolve_condition_image_path(cond_image_path)
        if handler_config.use_face_crop:
            from handlers.avatar.flashhead.flashhead_face_crop import crop_face
            try:
                cropped = crop_face(cond_image_path)
                cropped_path = cond_image_path + ".cropped.png"
                cropped.save(cropped_path)
                logger.info(f"FlashHead: face-cropped image saved to {cropped_path}")
                return cropped_path
            except Exception as e:
                logger.warning(f"FlashHead: face crop failed ({e}), using original image")
        return cond_image_path

    def _import_get_base_data(self):
        from flash_head.inference import get_base_data
        return get_base_data

    def update_condition_image(self, cond_image_path: str) -> str:
        if self.pipeline is None:
            raise RuntimeError("FlashHead pipeline is not loaded.")
        if not isinstance(self._handler_config, FlashHeadConfig):
            self._handler_config = FlashHeadConfig()
        prepared_path = self._prepare_condition_image(cond_image_path, self._handler_config)
        if not self._flashhead_algo_path:
            if not self.handler_root:
                raise RuntimeError("FlashHead handler root is not set.")
            self._flashhead_algo_path = os.path.join(self.handler_root, "SoulX-FlashHead")

        with self._condition_image_lock:
            original_cwd = os.getcwd()
            os.chdir(self._flashhead_algo_path)
            try:
                get_base_data = self._import_get_base_data()
                get_base_data(
                    pipeline=self.pipeline,
                    cond_image_path_or_dir=prepared_path,
                    base_seed=self._handler_config.base_seed,
                    use_face_crop=False,
                )
                if hasattr(self.pipeline, "rank"):
                    self.pipeline.rank = 1
                self._condition_image_path = prepared_path
                self._handler_config.cond_image_path = prepared_path
                logger.info(f"FlashHead condition image updated: {prepared_path}")
                return prepared_path
            finally:
                os.chdir(original_cwd)

    def create_context(self, session_context: SessionContext,
                       handler_config: Optional[FlashHeadConfig] = None) -> HandlerContext:
        if not isinstance(handler_config, FlashHeadConfig):
            handler_config = FlashHeadConfig()

        processor = FlashHeadProcessor(
            pipeline=self.pipeline,
            infer_params=copy.deepcopy(self.infer_params),
            output_audio_sample_rate=handler_config.output_audio_sample_rate,
            idle_noise_amplitude=handler_config.idle_noise_amplitude,
        )

        context = FlashHeadContext(
            session_context.session_info.session_id,
            processor,
        )
        context.output_data_definitions = self.output_data_definitions
        context.config = handler_config
        context.shared_states = session_context.shared_states

        callbacks = context._build_callbacks()
        processor.set_callbacks(callbacks)

        logger.info(f"FlashHead context created for session {session_context.session_info.session_id}")
        return context

    def start_context(self, session_context: SessionContext, handler_context: HandlerContext):
        context = cast(FlashHeadContext, handler_context)
        context.init_playback_streamer()
        # Start the frame collector thread (emits video at constant FPS)
        context.processor.start()
        logger.info(f"FlashHead context started for session {context.session_id}")

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        context = cast(FlashHeadContext, context)
        inputs = {
            ChatDataType.AVATAR_AUDIO: HandlerDataInfo(
                type=ChatDataType.AVATAR_AUDIO,
                input_consume_mode=ChatDataConsumeMode.ONCE,
            )
        }
        outputs = {
            ChatDataType.AVATAR_AUDIO: HandlerDataInfo(
                type=ChatDataType.AVATAR_AUDIO,
                definition=context.output_data_definitions[ChatDataType.AVATAR_AUDIO],
            ),
            ChatDataType.AVATAR_VIDEO: HandlerDataInfo(
                type=ChatDataType.AVATAR_VIDEO,
                definition=context.output_data_definitions[ChatDataType.AVATAR_VIDEO],
                output_stream_config=ChatStreamConfig(cancelable=False, auto_link_input=False),
            ),
        }
        return HandlerDetail(
            inputs=inputs, outputs=outputs,
            signal_filters=[
                # 监听 CLIENT_PLAYBACK 的 STREAM_CANCEL 信号（用于打断）
                SignalFilterRule(ChatSignalType.STREAM_CANCEL, None, ChatDataType.CLIENT_PLAYBACK),
                # 兜底监听全局打断以及 FlashHead/TTS 音频流取消，避免只取消外层音频流时
                # processor 仍继续输出旧帧。
                SignalFilterRule(ChatSignalType.INTERRUPT, None, None),
                SignalFilterRule(ChatSignalType.STREAM_CANCEL, None, ChatDataType.AVATAR_AUDIO),
            ]
        )

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        """Process AVATAR_AUDIO input: resample to 16kHz and feed to FlashHead processor."""
        if inputs.type != ChatDataType.AVATAR_AUDIO:
            return
        context = cast(FlashHeadContext, context)

        # --- Track TTS AVATAR_AUDIO stream via CLIENT_PLAYBACK lifecycle streams ---
        stream_key_str = inputs.stream_id.stream_key_str if inputs.stream_id else None
        with context._stream_key_lock:
            if stream_key_str and stream_key_str in context._interrupted_tts_stream_keys:
                logger.info(
                    f"FlashHead: drop interrupted TTS audio residue, stream_key={stream_key_str}"
                )
                return
            prev_key = context._current_tts_stream_key
            need_switch = bool(stream_key_str and stream_key_str != prev_key)
            if need_switch:
                context._current_tts_stream_key = stream_key_str

        self._apply_runtime_condition_image(context)

        if need_switch:
            # Reset processor interrupt state for new speech
            context.processor.reset_interrupt()

            streamer = context.get_playback_streamer()
            if streamer is not None and prev_key:
                streamer.finish_current()
                logger.info(
                    f"FlashHead: CLIENT_PLAYBACK stream closed (implicit) "
                    f"for previous stream_key={prev_key}"
                )
            if streamer is not None:
                sources = [inputs.stream_id] if inputs.stream_id else []
                streamer.open_stream(sources=sources, name=f"playback:{stream_key_str}")
                audit_event(
                    context,
                    "digital_human_start",
                    stream_identity=inputs.stream_id,
                    stream_key=stream_key_str,
                    avatar="flashhead",
                    success=None,
                )
                logger.info(f"FlashHead: CLIENT_PLAYBACK stream opened for stream_key={stream_key_str}")

        # --- Extract audio data ---
        speech_id = inputs.stream_id.stream_key_str if inputs.stream_id else None
        speech_end = inputs.is_last_data
        audio_entry = inputs.data.get_main_definition_entry()
        audio_array = inputs.data.get_main_data()

        if audio_array is None:
            audit_event(
                context,
                "digital_human_error",
                stream_identity=inputs.stream_id,
                stream_key=speech_id,
                avatar="flashhead",
                error="audio data is None",
                success=False,
            )
            audio_array = np.zeros([512], dtype=np.float32)
            logger.error(f"FlashHead: Audio data is None, fill with silence, speech_id: {speech_id}")

        if audio_array.dtype != np.float32:
            audio_array = audio_array.astype(np.float32)

        audio_array = audio_array.squeeze()

        # --- Resample from TTS output rate to FlashHead algo rate (e.g. 24kHz -> 16kHz) ---
        input_sample_rate = audio_entry.sample_rate
        algo_sample_rate = context.config.algo_audio_sample_rate

        if input_sample_rate != algo_sample_rate:
            import librosa
            audio_array_16k = librosa.resample(
                audio_array, orig_sr=input_sample_rate, target_sr=algo_sample_rate,
            )
        else:
            audio_array_16k = audio_array

        # --- Prepare original audio for synchronized output via frame collector ---
        original_audio = inputs.data.get_main_data()
        if original_audio is not None:
            original_audio = original_audio.astype(np.float32).squeeze()
        else:
            # Fallback: synthesize silence at the ratio matching 16kHz input
            ratio = context.config.output_audio_sample_rate / context.config.algo_audio_sample_rate
            original_audio = np.zeros(int(len(audio_array_16k) * ratio), dtype=np.float32)

        if context.config.debug:
            logger.info(
                f"FlashHead handle: speech_id={speech_id}, speech_end={speech_end}, "
                f"audio_16k.shape={audio_array_16k.shape}, "
                f"input_sr={input_sample_rate}, algo_sr={algo_sample_rate}"
            )

        # --- Feed to processor (buffering + inference + frame collector handles output) ---
        context.processor.add_audio(
            audio_data_16k=audio_array_16k,
            original_audio=original_audio,
            speech_id=speech_id,
            end_of_speech=speech_end,
        )


    def _apply_runtime_condition_image(self, context: FlashHeadContext) -> None:
        runtime = getattr(context.shared_states, "persona_runtime", None)
        persona_face_path = runtime.get("face_image_path") if isinstance(runtime, dict) else None
        target_path = persona_face_path or self._default_condition_image_path
        if not target_path or context._applied_condition_image_path == target_path:
            return
        if not os.path.exists(target_path):
            logger.warning(f"FlashHead: runtime condition image missing, fallback skipped: {target_path}")
            return
        try:
            applied_path = context.processor.apply_reference_update(
                lambda: self.update_condition_image(target_path)
            )
            context._applied_condition_image_path = target_path
            if persona_face_path:
                logger.info(
                    f"FlashHead: using persona face persona_id={runtime.get('persona_id')} image={applied_path}"
                )
            else:
                logger.info(f"FlashHead: using default face image={applied_path}")
        except Exception as exc:
            logger.opt(exception=exc).warning(f"FlashHead: failed to apply runtime condition image: {target_path}")

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        if not isinstance(context, FlashHeadContext):
            return
        if signal.type == ChatSignalType.INTERRUPT:
            logger.info("FlashHead: Received INTERRUPT signal, interrupting avatar")
            logger.info(
                f"INTERRUPT_TRACE flashhead_interrupt_received "
                f"session={context.session_id} source_name={signal.source_name} "
                f"mono={time.monotonic():.6f}"
            )
            context.interrupt()
            return

        if signal.type != ChatSignalType.STREAM_CANCEL or signal.related_stream is None:
            return

        related_stream = signal.related_stream
        should_interrupt = related_stream.data_type == ChatDataType.CLIENT_PLAYBACK
        if related_stream.data_type == ChatDataType.AVATAR_AUDIO:
            with context._stream_key_lock:
                current_tts_stream_key = context._current_tts_stream_key
            is_flashhead_output = related_stream.producer_name == "FlashHead"
            is_current_tts_stream = (
                current_tts_stream_key is not None
                and related_stream.stream_key_str == current_tts_stream_key
            )
            should_interrupt = is_flashhead_output or is_current_tts_stream

        if should_interrupt:
            logger.info("FlashHead: Received STREAM_CANCEL signal, interrupting avatar")
            logger.info(
                f"INTERRUPT_TRACE flashhead_cancel_received "
                f"session={context.session_id} stream={related_stream.stream_key_str} "
                f"stream_type={related_stream.data_type.value} "
                f"producer={related_stream.producer_name} "
                f"mono={time.monotonic():.6f}"
            )
            context.interrupt()

    def destroy_context(self, context: HandlerContext):
        """Clean up processor on session end."""
        if isinstance(context, FlashHeadContext):
            if context._playback_streamer is not None:
                try:
                    context._playback_streamer.finish_current()
                except Exception:
                    pass
            if context.processor is not None:
                context.processor.stop()
                context.processor.set_callbacks(None)
            context.clear()
            logger.info(f"FlashHead: Context destroyed for session {context.session_id}")

    def destroy(self):
        """Global cleanup."""
        self.pipeline = None
        self.infer_params = None
        logger.info("FlashHead: Handler destroyed")
