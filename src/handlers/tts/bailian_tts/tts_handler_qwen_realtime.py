import base64
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, cast

import dashscope
import numpy as np
from abc import ABC
from loguru import logger
from pydantic import BaseModel, Field

from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalType
from chat_engine.data_models.chat_stream import ChatStreamIdentity, StreamKey
from chat_engine.data_models.chat_stream_config import ChatStreamConfig
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from dashscope.audio.qwen_tts_realtime import AudioFormat, QwenTtsRealtime, QwenTtsRealtimeCallback
from engine_utils.directory_info import DirectoryInfo


class TTSConfig(HandlerBaseConfigModel, BaseModel):
    voice: str = Field(default="Eric")
    sample_rate: int = Field(default=24000)
    api_key: str = Field(default=os.getenv("DASHSCOPE_API_KEY"), repr=False)
    model_name: str = Field(default="qwen3-tts-flash-realtime")
    mode: str = Field(default="server_commit")


@dataclass
class QwenRealtimeTTSSession:
    input_stream_id: ChatStreamIdentity
    output_stream_key: Optional[StreamKey] = None
    synthesizer: Optional[QwenTtsRealtime] = None
    cancelled: bool = False

    def reset(self):
        self.cancelled = True
        synthesizer = self.synthesizer
        self.synthesizer = None
        if synthesizer is None:
            return
        try:
            synthesizer.cancel_response()
        except Exception:
            pass
        try:
            synthesizer.close()
        except Exception:
            pass


def decode_audio_delta(message: dict) -> bytes:
    if message.get("type") != "response.audio.delta":
        return b""
    delta = message.get("delta")
    if not isinstance(delta, str):
        return b""
    return base64.b64decode(delta)


class TTSContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config = None
        self.api_links: Dict[StreamKey, QwenRealtimeTTSSession] = {}
        self.dump_audio = False
        self.audio_dump_file = None

    @classmethod
    def _create_session(cls, input_stream: ChatStreamIdentity) -> QwenRealtimeTTSSession:
        return QwenRealtimeTTSSession(input_stream_id=input_stream)

    def handle_text_stream(self, data: ChatData, handler: 'HandlerTTS'):
        input_stream = data.stream_id
        input_stream_key = input_stream.key

        session = self.api_links.get(input_stream_key)
        if session is None:
            for old_key, old_session in list(self.api_links.items()):
                logger.info(f"TTS: Cancelling previous Qwen realtime session for stream {old_key}")
                old_session.reset()
            self.api_links.clear()

            session = self._create_session(input_stream)
            self.api_links[input_stream_key] = session

            streamer = self.data_submitter.get_streamer(ChatDataType.AVATAR_AUDIO)
            output_stream_id = streamer.new_stream(
                sources=[session.input_stream_id],
                name="qwen_realtime_tts",
                config=ChatStreamConfig(cancelable=True),
            )
            session.output_stream_key = output_stream_id.key

        text = data.data.get_main_data()
        if text is not None:
            text = re.sub(r"<\|.*?\|>", "", text)

        text_end = data.is_last_data

        try:
            if session.synthesizer is None:
                streamer = self.data_submitter.get_streamer(ChatDataType.AVATAR_AUDIO)
                callback = QwenRealtimeCallback(
                    context=self,
                    output_definition=streamer.data_definition,
                    session=session,
                )
                session.synthesizer = QwenTtsRealtime(model=handler.model_name, callback=callback)
                session.synthesizer.connect()
                session.synthesizer.update_session(
                    voice=handler.voice,
                    response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    mode=handler.mode,
                    sample_rate=handler.sample_rate,
                )

            if text:
                logger.info(f'qwen_realtime_append_text {text}')
                session.synthesizer.append_text(text)

            if text_end and session.synthesizer is not None:
                logger.info('qwen_realtime_finish')
                session.synthesizer.finish()
        except Exception as e:
            logger.error(e)
            session.reset()
            self.api_links.pop(input_stream_key, None)


class HandlerTTS(HandlerBase, ABC):
    def __init__(self):
        super().__init__()
        self.voice = None
        self.sample_rate = None
        self.model_name = None
        self.api_key = None
        self.mode = None

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(config_model=TTSConfig)

    def get_handler_detail(self, session_context: SessionContext, context: HandlerContext) -> HandlerDetail:
        definition = DataBundleDefinition()
        definition.add_entry(DataBundleEntry.create_audio_entry("avatar_audio", 1, self.sample_rate))
        inputs = [HandlerDataInfo(type=ChatDataType.AVATAR_TEXT)]
        outputs = [
            HandlerDataInfo(
                type=ChatDataType.AVATAR_AUDIO,
                definition=definition,
            )
        ]
        return HandlerDetail(
            inputs=inputs,
            outputs=outputs,
            signal_filters=[SignalFilterRule(ChatSignalType.STREAM_CANCEL, None, None)],
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[BaseModel] = None):
        config = cast(TTSConfig, handler_config)
        self.voice = config.voice
        self.sample_rate = config.sample_rate
        self.model_name = config.model_name
        self.api_key = config.api_key
        self.mode = config.mode
        if 'DASHSCOPE_API_KEY' in os.environ:
            dashscope.api_key = os.environ['DASHSCOPE_API_KEY']
        else:
            dashscope.api_key = config.api_key

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, TTSConfig):
            handler_config = TTSConfig()
        context = TTSContext(session_context.session_info.session_id)
        if context.dump_audio:
            dump_file_path = os.path.join(
                DirectoryInfo.get_project_dir(),
                'temp',
                f"dump_avatar_audio_{context.session_id}_{time.localtime().tm_hour}_{time.localtime().tm_min}.pcm",
            )
            context.audio_dump_file = open(dump_file_path, "wb")
        return context

    def start_context(self, session_context, context: HandlerContext):
        context = cast(TTSContext, context)

    def handle(self, context: HandlerContext, inputs: ChatData, output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        context = cast(TTSContext, context)
        if inputs.type == ChatDataType.AVATAR_TEXT:
            context.handle_text_stream(inputs, self)

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        context = cast(TTSContext, context)
        if signal.type == ChatSignalType.STREAM_CANCEL and signal.related_stream:
            stream_key = signal.related_stream.key
            if stream_key is None:
                return
            session = context.api_links.pop(stream_key, None)
            if session:
                logger.info(f"TTS: Cancelling Qwen realtime session for input stream {stream_key}")
                session.reset()
                return
            for key, session in list(context.api_links.items()):
                if session.output_stream_key == stream_key:
                    logger.info(f"TTS: Cancelling Qwen realtime session for output stream {stream_key}")
                    session.reset()
                    context.api_links.pop(key, None)
                    return

    def destroy_context(self, context: HandlerContext):
        context = cast(TTSContext, context)
        logger.info('destroy qwen realtime tts context')
        for session in context.api_links.values():
            try:
                session.reset()
            except Exception as e:
                logger.opt(exception=e).warning("Failed to reset Qwen realtime TTS session on destroy")
        context.api_links.clear()
        if context.audio_dump_file is not None:
            try:
                context.audio_dump_file.close()
            except Exception:
                pass


class QwenRealtimeCallback(QwenTtsRealtimeCallback):
    def __init__(self, context: TTSContext, output_definition, session: QwenRealtimeTTSSession):
        self.context = context
        self.output_definition = output_definition
        self.session = session
        self.temp_bytes = b''
        self._finished = False

    @property
    def is_cancelled(self) -> bool:
        return self.session.cancelled

    def on_open(self) -> None:
        logger.info('TTS: Qwen realtime WebSocket connected')

    def on_event(self, message) -> None:
        if self.is_cancelled:
            return
        event_type = message.get("type") if isinstance(message, dict) else None
        if event_type == "response.audio.delta":
            self._on_data(decode_audio_delta(message))
        elif event_type == "response.done":
            self._flush_audio()
            self._submit_end_frame()
        elif event_type and ("error" in event_type or event_type.endswith("failed")):
            logger.error(f'TTS: Qwen realtime service error event: {message}')
            self._submit_end_frame()

    def on_close(self, close_status_code, close_msg) -> None:
        if self.is_cancelled:
            self.temp_bytes = b''
            logger.info('TTS: Qwen realtime synthesis cancelled, skipping output in on_close')
            return
        logger.info(f'TTS: Qwen realtime WebSocket closed: {close_status_code} {close_msg}')
        self._flush_audio()
        self._submit_end_frame()

    def _on_data(self, data: bytes) -> None:
        if self.is_cancelled or not data:
            return
        self.temp_bytes += data
        if len(self.temp_bytes) > 24000:
            self._flush_audio()

    def _flush_audio(self) -> None:
        if self.is_cancelled or not self.temp_bytes:
            self.temp_bytes = b''
            return
        output_audio = np.array(np.frombuffer(self.temp_bytes, dtype=np.int16)).astype(np.float32) / 32767
        output_audio = output_audio[np.newaxis, ...]
        output = DataBundle(self.output_definition)
        output.set_main_data(output_audio)
        self.context.submit_data(output)
        self.temp_bytes = b''

    def _submit_end_frame(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self.is_cancelled:
            return
        output = DataBundle(self.output_definition)
        output.set_main_data(np.zeros(shape=(1, 240), dtype=np.float32))
        self.context.submit_data(output, finish_stream=True)
        self.context.api_links.pop(self.session.input_stream_id.key, None)
