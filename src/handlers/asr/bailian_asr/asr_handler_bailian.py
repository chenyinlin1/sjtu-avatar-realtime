


import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, cast
from loguru import logger
import numpy as np
from pydantic import BaseModel, Field
from abc import ABC
import os
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalType
from chat_engine.data_models.chat_stream import StreamKey, ChatStreamIdentity
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from chat_engine.contexts.session_context import SessionContext
import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from engine_utils.directory_info import DirectoryInfo
from engine_utils.general_slicer import SliceContext, slice_data
from engine_utils.conversation_audit_logger import audit_event


class BailianASRConfig(HandlerBaseConfigModel, BaseModel):
    model_name: str = Field(default="fun-asr-realtime")
    sample_rate: int = Field(default=16000)
    format: str = Field(default="pcm")
    api_key: str = Field(default=os.getenv("DASHSCOPE_API_KEY"))
    semantic_punctuation_enabled: bool = Field(default=False)
    language_hints: Optional[List[str]] = Field(default=None)
    # WebSocket endpoint required by DashScope ASR SDK
    base_websocket_url: str = Field(default="wss://dashscope.aliyuncs.com/api-ws/v1/inference")
    final_silence_padding_ms: int = Field(default=500)


class BailianASRCallback(RecognitionCallback):
    """Callback for DashScope streaming Recognition, collects sentence text."""

    def __init__(self):
        super().__init__()
        self.sentences = []
        self.error_message = None
        self.completed = threading.Event()

    def on_open(self) -> None:
        logger.info('BailianASR: WebSocket connected')

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if 'text' in sentence:
            if RecognitionResult.is_sentence_end(sentence):
                self.sentences.append(sentence['text'])
                logger.info(
                    'BailianASR sentence end, request_id:%s, text:%s'
                    % (result.get_request_id(), sentence['text']))

    def on_complete(self) -> None:
        logger.info('BailianASR: Recognition completed')
        self.completed.set()

    def on_error(self, result: RecognitionResult) -> None:
        self.error_message = result.message
        logger.error('BailianASR error, request_id: %s, error: %s'
                     % (result.request_id, result.message))
        self.completed.set()

    def on_close(self) -> None:
        logger.info('BailianASR: WebSocket closed')
        self.completed.set()

    def get_full_text(self) -> str:
        return ''.join(self.sentences)


@dataclass
class BailianASRSession:
    """Per-stream session state, isolates audio buffers for each input stream."""
    input_stream_id: ChatStreamIdentity
    output_audios: list = field(default_factory=list)
    audio_slice_context: Optional[SliceContext] = None
    callback: Optional[BailianASRCallback] = None
    recognition: Optional[Recognition] = None
    sent_sample_count: int = 0

    def __post_init__(self):
        self.audio_slice_context = SliceContext.create_numpy_slice_context(
            # 100ms at 16kHz; DashScope receives audio while the user is speaking.
            slice_size=1600,
            slice_axis=0,
        )

    def stop_recognition(self):
        recognition = self.recognition
        self.recognition = None
        if recognition is None:
            return
        try:
            recognition.stop()
        except Exception as e:
            logger.debug(f"BailianASR: recognition already stopped or failed to stop cleanly: {e}")

    def reset(self):
        self.stop_recognition()
        if self.audio_slice_context is not None:
            self.audio_slice_context.flush()
        self.output_audios.clear()
        self.callback = None
        self.sent_sample_count = 0


class BailianASRContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config = None
        self.api_links: Dict[StreamKey, BailianASRSession] = {}
        self.shared_states = None

        self.dump_audio = True
        self.audio_dump_file = None
        if self.dump_audio:
            dump_file_path = os.path.join(DirectoryInfo.get_project_dir(),
                                          "dump_talk_audio.pcm")
            self.audio_dump_file = open(dump_file_path, "wb")

    @classmethod
    def _create_session(cls, input_stream: ChatStreamIdentity) -> BailianASRSession:
        return BailianASRSession(input_stream_id=input_stream)

    @staticmethod
    def _convert_audio_to_pcm_bytes(audio: np.ndarray) -> bytes:
        audio_int16 = (audio * 32767).astype(np.int16)
        return audio_int16.tobytes()

    @staticmethod
    def _normalize_audio(audio) -> np.ndarray:
        audio = np.asarray(audio).squeeze()
        if audio.ndim == 0:
            audio = audio.reshape(1)
        return audio

    def _start_recognition_if_needed(self, session: BailianASRSession, handler: 'HandlerASR',
                                     input_stream_key: StreamKey):
        if session.recognition is not None:
            return

        callback = BailianASRCallback()
        recognition_kwargs = {
            'model': handler.model_name,
            'format': handler.audio_format,
            'sample_rate': handler.sample_rate,
            'semantic_punctuation_enabled': handler.semantic_punctuation_enabled,
            'callback': callback,
        }
        if handler.language_hints:
            recognition_kwargs['language_hints'] = handler.language_hints

        recognition = Recognition(**recognition_kwargs)
        session.callback = callback
        session.recognition = recognition
        recognition.start()
        logger.info(f"BailianASR: streaming recognition started for {input_stream_key}")

    def _send_audio_segment(self, session: BailianASRSession, audio_segment: np.ndarray,
                            include_in_audit: bool = True):
        if audio_segment is None or audio_segment.shape[0] == 0:
            return
        if session.recognition is None:
            raise RuntimeError("BailianASR recognition is not started")

        if include_in_audit:
            session.output_audios.append(audio_segment)
            session.sent_sample_count += int(audio_segment.shape[0])
        session.recognition.send_audio_frame(
            self._convert_audio_to_pcm_bytes(audio_segment)
        )

    def _send_final_silence_padding(self, session: BailianASRSession, handler: 'HandlerASR'):
        padding_samples = int(handler.sample_rate * handler.final_silence_padding_ms / 1000)
        if padding_samples <= 0:
            return
        silence = np.zeros(shape=(padding_samples,), dtype=np.float32)
        self._send_audio_segment(session, silence, include_in_audit=False)

    def _emit_error_text(self, session: BailianASRSession, input_stream_key: StreamKey, error_message: str):
        output_streamer = None
        if self.data_submitter is not None:
            output_streamer = self.data_submitter.get_streamer(ChatDataType.HUMAN_TEXT)
        if output_streamer is None:
            logger.warning(f"BailianASR: No HUMAN_TEXT streamer for error on {input_stream_key}")
            return
        try:
            output_streamer.new_stream([session.input_stream_id])
            if output_streamer.current_stream is not None:
                error_data = DataBundle(output_streamer.data_definition)
                error_data.set_main_data("")
                error_data.add_meta("human_text_end", True)
                error_data.add_meta("error", True)
                error_data.add_meta("error_message", error_message[:200])
                output_streamer.stream_data(error_data, finish_stream=True)
                logger.info(f"BailianASR: Finished output stream on error for {input_stream_key}")
        except Exception as e:
            logger.warning(f"BailianASR: Failed to finish output stream on error: {e}")

    @staticmethod
    def _log_recognition_metrics(recognition: Recognition):
        try:
            logger.info(
                '[Metric] requestId: {}, first package delay ms: {}, last package delay ms: {}'
                .format(
                    recognition.get_last_request_id(),
                    recognition.get_first_package_delay(),
                    recognition.get_last_package_delay(),
                ))
        except Exception as e:
            logger.debug(f"BailianASR: Failed to log recognition metrics: {e}")

    def _finish_recognition_session(self, input_stream_key: StreamKey, session: BailianASRSession,
                                    handler: 'HandlerASR'):
        if session.recognition is None or session.callback is None:
            self.api_links.pop(input_stream_key, None)
            return

        output_audio = np.concatenate(session.output_audios) if session.output_audios else None
        if output_audio is not None and self.audio_dump_file is not None:
            logger.info('dump audio')
            self.audio_dump_file.write(output_audio.tobytes())

        recognition = session.recognition
        callback = session.callback
        session.recognition = None
        try:
            try:
                recognition.stop()
            except Exception as e:
                logger.error(f"BailianASR recognition stop exception: {e}")
                self._emit_error_text(session, input_stream_key, str(e))
                return

            callback.completed.wait(timeout=30)

            if callback.error_message:
                logger.error(f"BailianASR recognition error: {callback.error_message}")
                self._emit_error_text(session, input_stream_key, callback.error_message)
                return

            output_text = callback.get_full_text()
            logger.info(f"BailianASR result: {output_text}")

            if len(output_text) == 0:
                return

            output_streamer = None
            if self.data_submitter is not None:
                output_streamer = self.data_submitter.get_streamer(ChatDataType.HUMAN_TEXT)
            if output_streamer is None:
                logger.warning(f"BailianASR: No HUMAN_TEXT streamer for {input_stream_key}")
                return

            # Use streamer with explicit source stream linkage for 1:1 mapping.
            output_streamer.new_stream([session.input_stream_id])

            if output_streamer.current_stream is None:
                logger.info(f"BailianASR: Output stream was auto-cancelled for {input_stream_key}, skipping output")
                return

            output_stream_key = output_streamer.current_stream.identity.stream_key_str
            audit_event(
                self,
                "asr_transcript",
                stream_identity=session.input_stream_id,
                bind_stream_key=output_stream_key,
                create_turn=True,
                provider="bailian_asr",
                model=handler.model_name,
                transcript=output_text,
                success=True,
                audio_samples=session.sent_sample_count,
                audio_dump_path=getattr(self.audio_dump_file, "name", None),
            )
            output = DataBundle(output_streamer.data_definition)
            output.set_main_data(output_text)
            output.add_meta("human_text_end", True)
            logger.info(f"BailianASR: {output_text}")
            output_streamer.stream_data(output, finish_stream=True)
        except Exception as e:
            logger.error(f"BailianASR recognition exception: {e}")
        finally:
            self._log_recognition_metrics(recognition)
            session.reset()
            self.api_links.pop(input_stream_key, None)

    def handle_audio_stream(self, data: ChatData, handler: 'HandlerASR'):
        input_stream = data.stream_id
        input_stream_key = input_stream.key
        session = self.api_links.get(input_stream_key)
        if session is None:
            session = self._create_session(input_stream)
            self.api_links[input_stream_key] = session

        audio = data.data.get_main_data() if data.data is not None else None
        if audio is not None:
            audio = self._normalize_audio(audio)
            if audio.shape[0] > 0:
                logger.info('audio in')
                try:
                    self._start_recognition_if_needed(session, handler, input_stream_key)
                    for audio_segment in slice_data(session.audio_slice_context, audio):
                        self._send_audio_segment(session, audio_segment)
                except Exception as e:
                    logger.error(f"BailianASR recognition exception: {e}")
                    self._emit_error_text(session, input_stream_key, str(e))
                    session.reset()
                    self.api_links.pop(input_stream_key, None)
                    return

        if not data.is_last_data:
            return

        remainder_audio = session.audio_slice_context.flush()
        try:
            if remainder_audio is not None and remainder_audio.shape[0] > 0:
                self._start_recognition_if_needed(session, handler, input_stream_key)
                self._send_audio_segment(session, remainder_audio)
        except Exception as e:
            logger.error(f"BailianASR recognition exception: {e}")
            self._emit_error_text(session, input_stream_key, str(e))
            session.reset()
            self.api_links.pop(input_stream_key, None)
            return

        if session.sent_sample_count == 0:
            session.reset()
            self.api_links.pop(input_stream_key, None)
            return

        try:
            self._send_final_silence_padding(session, handler)
        except Exception as e:
            logger.error(f"BailianASR final silence padding exception: {e}")
            self._emit_error_text(session, input_stream_key, str(e))
            session.reset()
            self.api_links.pop(input_stream_key, None)
            return

        self._finish_recognition_session(input_stream_key, session, handler)


class HandlerASR(HandlerBase, ABC):
    def __init__(self):
        super().__init__()

        self.model_name = 'fun-asr-realtime'
        self.sample_rate = 16000
        self.audio_format = 'pcm'
        self.semantic_punctuation_enabled = False
        self.language_hints = None
        self.final_silence_padding_ms = 500

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            name="ASR_Bailian",
            config_model=BailianASRConfig,
        )

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        definition = DataBundleDefinition()
        definition.add_entry(DataBundleEntry.create_text_entry("human_text"))
        inputs = [
            HandlerDataInfo(type=ChatDataType.HUMAN_AUDIO),
        ]
        outputs = [
            HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT,
                definition=definition,
            )
        ]
        return HandlerDetail(
            inputs=inputs,
            outputs=outputs,
            signal_filters=[
                SignalFilterRule(ChatSignalType.STREAM_CANCEL, None, None)
            ]
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[BaseModel] = None):
        config = cast(BailianASRConfig, handler_config)
        self.model_name = config.model_name
        self.sample_rate = config.sample_rate
        self.audio_format = config.format
        self.semantic_punctuation_enabled = config.semantic_punctuation_enabled
        self.language_hints = config.language_hints
        self.final_silence_padding_ms = config.final_silence_padding_ms

        if 'DASHSCOPE_API_KEY' in os.environ:
            # load API-key from environment variable DASHSCOPE_API_KEY
            dashscope.api_key = os.environ['DASHSCOPE_API_KEY']
        else:
            dashscope.api_key = config.api_key  # set API-key manually

        # WebSocket endpoint required by DashScope ASR SDK
        dashscope.base_websocket_api_url = config.base_websocket_url

        logger.info(f"BailianASR loaded, model={self.model_name}, "
                    f"sample_rate={self.sample_rate}, format={self.audio_format}, "
                    f"final_silence_padding_ms={self.final_silence_padding_ms}, "
                    f"base_websocket_url={config.base_websocket_url}")

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, BailianASRConfig):
            handler_config = BailianASRConfig()
        context = BailianASRContext(session_context.session_info.session_id)
        context.shared_states = session_context.shared_states
        return context

    def start_context(self, session_context, handler_context):
        pass

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        context = cast(BailianASRContext, context)
        if inputs.type == ChatDataType.HUMAN_AUDIO:
            context.handle_audio_stream(inputs, self)

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        """处理 STREAM_CANCEL 信号，终止被取消的 stream 的处理"""
        context = cast(BailianASRContext, context)
        if signal.type == ChatSignalType.STREAM_CANCEL and signal.related_stream:
            stream_key = signal.related_stream.key
            # 如果 session 还在处理中，关闭它
            session = context.api_links.pop(stream_key, None)
            if session:
                logger.info(f"BailianASR: Cancelling session for stream {stream_key}")
                session.reset()

    def destroy_context(self, context: HandlerContext):
        context = cast(BailianASRContext, context)
        for session in context.api_links.values():
            # 可能有尚未建立成功的连接，判空并兜底重置
            try:
                session.reset()
            except Exception as e:
                logger.opt(exception=e).warning("Failed to reset BailianASR session on destroy")
        context.api_links.clear()
        if context.audio_dump_file is not None:
            try:
                context.audio_dump_file.close()
            except Exception:
                pass
