

import re
import time
from typing import Dict, Optional, cast
from loguru import logger
import numpy as np
from pydantic import BaseModel, Field
from abc import ABC
import os
import torch
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from chat_engine.contexts.session_context import SessionContext
from funasr import AutoModel

from engine_utils.directory_info import DirectoryInfo
from engine_utils.general_slicer import SliceContext, slice_data
from engine_utils.conversation_audit_logger import audit_event


class ASRConfig(HandlerBaseConfigModel, BaseModel):
    model_name: str = Field(default="iic/SenseVoiceSmall")


class ASRContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config = None
        self.local_session_id = 0
        self.output_audios = []
        self.audio_slice_context = SliceContext.create_numpy_slice_context(
            slice_size=16000,
            slice_axis=0,
        )
        self.cache = {}
        self.current_audio_stream_key: Optional[str] = None
        self.current_asr_stream_start_mono: Optional[float] = None
        self.current_asr_audio_samples: int = 0
        self.shared_states = None

        self.dump_audio = True
        self.audio_dump_file = None
        if self.dump_audio:
            dump_file_path = os.path.join(DirectoryInfo.get_project_dir(),
                                          "dump_talk_audio.pcm")
            self.audio_dump_file = open(dump_file_path, "wb")


class HandlerASR(HandlerBase, ABC):
    def __init__(self):
        super().__init__()

        self.model_name = 'iic/SenseVoiceSmall'

        if torch.cuda.is_available():
            self.device = torch.device("cuda:0")
        elif torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            name="ASR_Funasr",
            config_model=ASRConfig,
        )

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        definition = DataBundleDefinition()
        definition.add_entry(DataBundleEntry.create_audio_entry("avatar_audio", 1, 24000))
        inputs = {
            ChatDataType.HUMAN_AUDIO: HandlerDataInfo(
                type=ChatDataType.HUMAN_AUDIO,
            )
        }
        outputs = {
            ChatDataType.HUMAN_TEXT: HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT,
                definition=definition,
            )
        }
        return HandlerDetail(
            inputs=inputs, outputs=outputs,
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[BaseModel] = None):
        if isinstance(handler_config, ASRConfig):
            self.model_name = handler_config.model_name
            model_path = os.path.join(DirectoryInfo.get_models_dir(), handler_config.model_name)
            if os.path.exists(model_path):
                self.model_name = model_path
        logger.info(f"load model {self.model_name}")
        self.model = AutoModel(model=self.model_name, disable_update=True)

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, ASRConfig):
            handler_config = ASRConfig()
        context = ASRContext(session_context.session_info.session_id)
        context.shared_states = session_context.shared_states
        return context

    def start_context(self, session_context, handler_context):
        pass

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):

        output_definition = output_definitions.get(ChatDataType.HUMAN_TEXT).definition
        context = cast(ASRContext, context)
        if inputs.type == ChatDataType.HUMAN_AUDIO:
            audio = inputs.data.get_main_data()
        else:
            return

        stream_key = inputs.stream_id.stream_key_str if inputs.stream_id else None
        if stream_key != context.current_audio_stream_key or context.current_asr_stream_start_mono is None:
            context.current_audio_stream_key = stream_key
            context.current_asr_stream_start_mono = time.monotonic()
            context.current_asr_audio_samples = 0
            logger.info(
                f"INTERRUPT_TRACE asr_audio_stream_begin "
                f"session={context.session_id} stream={stream_key} "
                f"mono={context.current_asr_stream_start_mono:.6f} input_type={inputs.type.value}"
            )

        if audio is not None:
            audio = audio.squeeze()
            context.current_asr_audio_samples += int(audio.shape[0])

            logger.info('audio in')
            for audio_segment in slice_data(context.audio_slice_context, audio):
                if audio_segment is None or audio_segment.shape[0] == 0:
                    continue
                context.output_audios.append(audio_segment)

        speech_end = inputs.is_last_data
        if not speech_end:
            return

        # prefill remainder audio in slice context
        remainder_audio = context.audio_slice_context.flush()
        if remainder_audio is not None:
            if remainder_audio.shape[0] < context.audio_slice_context.slice_size:
                remainder_audio = np.concatenate(
                    [remainder_audio,
                     np.zeros(shape=(context.audio_slice_context.slice_size - remainder_audio.shape[0]))])
                context.output_audios.append(remainder_audio)
        output_audio = np.concatenate(context.output_audios)
        if context.audio_dump_file is not None:
            logger.info('dump audio')
            context.audio_dump_file.write(output_audio.tobytes())

        asr_start_mono = time.monotonic()
        since_stream_start_ms = (
            (asr_start_mono - context.current_asr_stream_start_mono) * 1000
            if context.current_asr_stream_start_mono is not None else -1
        )
        logger.info(
            f"INTERRUPT_TRACE asr_generate_start "
            f"session={context.session_id} stream={context.current_audio_stream_key} "
            f"mono={asr_start_mono:.6f} accumulated_samples={output_audio.shape[0]} "
            f"received_samples={context.current_asr_audio_samples} "
            f"since_stream_start_ms={since_stream_start_ms:.1f}"
        )
        res = self.model.generate(input=output_audio, batch_size_s=10)
        asr_done_mono = time.monotonic()
        logger.info(res)
        context.output_audios.clear()
        output_text = re.sub(r"<\|.*?\|>", "", res[0]['text'])
        logger.info(
            f"INTERRUPT_TRACE asr_generate_done "
            f"session={context.session_id} stream={context.current_audio_stream_key} "
            f"mono={asr_done_mono:.6f} duration_ms={(asr_done_mono - asr_start_mono) * 1000:.1f} "
            f"output_text_len={len(output_text)}"
        )
        if len(output_text) == 0:
            return
        audit_event(
            context,
            "asr_transcript",
            stream_identity=inputs.stream_id,
            create_turn=True,
            provider="sensevoice",
            model=self.model_name,
            transcript=output_text,
            success=True,
            audio_samples=int(output_audio.shape[0]),
            audio_dump_path=getattr(context.audio_dump_file, "name", None),
        )
        output = DataBundle(output_definition)
        output.set_main_data(output_text)
        context.submit_data(output, finish_stream=True)

    def destroy_context(self, context: HandlerContext):
        pass
