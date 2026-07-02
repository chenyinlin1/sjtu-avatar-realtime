

import os
import re
from typing import Dict, Optional, Set, cast
from loguru import logger
from pydantic import BaseModel, Field
from abc import ABC
from openai import APIStatusError, OpenAI
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalType
from chat_engine.data_models.chat_stream import StreamKey
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from .chat_history_manager import ChatHistory, HistoryMessage
from .search_engine import format_search_results, search_bocha
from chat_engine.data_models.chat_stream_config import ChatStreamConfig

try:
    from handlers.agent.tools.music_request import MusicRequestTool
except Exception:
    MusicRequestTool = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"Invalid float env {name}={value}, use default {default}")
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Invalid int env {name}={value}, use default {default}")
        return default


class LLMConfig(HandlerBaseConfigModel, BaseModel):
    model_name: str = Field(default="qwen-plus")
    system_prompt: str = Field(default="请你扮演一个 AI 助手，用简短的对话来回答用户的问题，并在对话内容中加入合适的标点符号，不需要加入标点符号相关的内容")
    api_key: str = Field(default=os.getenv("DASHSCOPE_API_KEY"), repr=False)
    api_url: str = Field(default=None)
    enable_video_input: bool = Field(default=False)
    history_length: int = Field(default=20)
    web_search_mode: str = Field(default=os.getenv("OPENAVATAR_WEB_SEARCH_MODE", "off"))
    web_search_always: bool = Field(default=_env_bool("OPENAVATAR_WEB_SEARCH_ALWAYS", False))
    bocha_api_key: str = Field(default=os.getenv("BOCHA_API_KEY"), repr=False)
    bocha_endpoint: str = Field(default=os.getenv("BOCHA_ENDPOINT", "https://api.bochaai.com/v1/web-search"))
    web_search_timeout: float = Field(default=_env_float("OPENAVATAR_WEB_SEARCH_TIMEOUT", 3.0))
    web_search_result_limit: int = Field(default=_env_int("OPENAVATAR_WEB_SEARCH_RESULT_LIMIT", 5))


class LLMContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config = None
        self.local_session_id = 0
        self.model_name = None
        self.system_prompt = None
        self.api_key = None
        self.api_url = None
        self.client = None
        self.input_texts = ""
        self.output_texts = ""
        self.current_image = None
        self.history = None
        self.enable_video_input = False
        self.active_stream_keys: Set[StreamKey] = set()
        self.web_search_mode = "off"
        self.web_search_always = False
        self.bocha_api_key = None
        self.bocha_endpoint = None
        self.web_search_timeout = 3.0
        self.web_search_result_limit = 5


class HandlerLLM(HandlerBase, ABC):
    def __init__(self):
        super().__init__()

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            config_model=LLMConfig,
        )

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        definition = DataBundleDefinition()
        definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))
        audio_definition = DataBundleDefinition()
        audio_definition.add_entry(DataBundleEntry.create_audio_entry("avatar_audio", 1, 24000))
        inputs = {
            ChatDataType.HUMAN_TEXT: HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT,
            ),
            ChatDataType.CAMERA_VIDEO: HandlerDataInfo(
                type=ChatDataType.CAMERA_VIDEO,
            ),
        }
        outputs = {
            ChatDataType.AVATAR_TEXT: HandlerDataInfo(
                type=ChatDataType.AVATAR_TEXT,
                definition=definition,
            ),
            ChatDataType.AVATAR_AUDIO: HandlerDataInfo(
                type=ChatDataType.AVATAR_AUDIO,
                definition=audio_definition,
            ),
        }
        return HandlerDetail(
            inputs=inputs, 
            outputs=outputs,
            signal_filters=[
                SignalFilterRule(ChatSignalType.STREAM_CANCEL, None, None)
            ]
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[BaseModel] = None):
        if isinstance(handler_config, LLMConfig):
            if handler_config.api_key is None or len(handler_config.api_key) == 0:
                error_message = 'api_key is required in config/xxx.yaml, when use handler_llm'
                logger.error(error_message)
                raise ValueError(error_message)

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, LLMConfig):
            handler_config = LLMConfig()
        context = LLMContext(session_context.session_info.session_id)
        context.model_name = handler_config.model_name
        context.system_prompt = {'role': 'system', 'content': handler_config.system_prompt}
        context.api_key = handler_config.api_key
        context.api_url = handler_config.api_url
        context.enable_video_input = handler_config.enable_video_input
        context.history = ChatHistory(history_length=handler_config.history_length)
        context.web_search_mode = os.getenv(
            "OPENAVATAR_WEB_SEARCH_MODE",
            handler_config.web_search_mode or "off",
        ).strip().lower()
        context.web_search_always = _env_bool("OPENAVATAR_WEB_SEARCH_ALWAYS", handler_config.web_search_always)
        context.bocha_api_key = os.getenv("BOCHA_API_KEY", handler_config.bocha_api_key or "")
        context.bocha_endpoint = os.getenv("BOCHA_ENDPOINT", handler_config.bocha_endpoint)
        context.web_search_timeout = _env_float("OPENAVATAR_WEB_SEARCH_TIMEOUT", handler_config.web_search_timeout)
        context.web_search_result_limit = _env_int(
            "OPENAVATAR_WEB_SEARCH_RESULT_LIMIT",
            handler_config.web_search_result_limit,
        )
        logger.info(f"LLM web search mode: {context.web_search_mode}")
        context.client =    OpenAI(  
            # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx",
            api_key=context.api_key,
            base_url=context.api_url,
            timeout=5.0,  # 30秒超时，避免 API 无响应时阻塞整个系统
        )
        return context
    
    def start_context(self, session_context, handler_context):
        pass

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        output_definition = output_definitions.get(ChatDataType.AVATAR_TEXT).definition
        context = cast(LLMContext, context)

        streamer = context.data_submitter.get_streamer(ChatDataType.AVATAR_TEXT)
        if inputs.type == ChatDataType.CAMERA_VIDEO and context.enable_video_input:
            context.current_image = inputs.data.get_main_data()
            return
        elif inputs.type == ChatDataType.HUMAN_TEXT:
            text = inputs.data.get_main_data()
        else:
            return

        stream_key = streamer.current_stream.identity.stream_key_str if streamer.current_stream is not None else None
        if stream_key is None:
            stream = streamer.new_stream(sources=[inputs.stream_id], name="openai_compatible", config=ChatStreamConfig(cancelable=True))
            stream_key = stream.stream_key_str

        if text is not None:
            context.input_texts += text

        text_end = inputs.is_last_data
        if not text_end:
            return

        chat_text = context.input_texts
        chat_text = re.sub(r"<\|.*?\|>", "", chat_text)
        if len(chat_text) < 1:
            logger.warning("LLM got empty query, return emtpy response.")
            end_output = DataBundle(output_definition)
            end_output.set_main_data('')
            streamer.stream_data(end_output, name="openai_compatible", config=ChatStreamConfig(cancelable=True), finish_stream=True)
            return
        logger.info(f'llm input {context.model_name} {chat_text} ')
        music_query = self._extract_music_request(chat_text)
        if music_query:
            logger.info(f"Music request detected: {music_query}")
            self._handle_music_request(
                context,
                music_query,
                output_definition,
                output_definitions,
                streamer,
                stream_key,
                chat_text,
                inputs.stream_id,
            )
            return
        current_content = context.history.generate_next_messages(chat_text, 
                                                                 [context.current_image] if context.current_image is not None else [])
        logger.debug(f'llm input {context.model_name} {current_content} ')
        if stream_key:
            context.active_stream_keys.add(stream_key)
        cancelled = False
        try:
            messages = [
                context.system_prompt,
            ] + current_content
            if context.web_search_mode == "bocha":
                search_context = self._build_bocha_search_context(context, chat_text)
                if search_context:
                    messages.insert(1, {
                        "role": "system",
                        "content": search_context,
                    })

            create_kwargs = {}
            if context.web_search_mode == "dashscope":
                create_kwargs["extra_body"] = {"enable_search": True}

            completion = context.client.chat.completions.create(
                model=context.model_name,  # 此处以qwen-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **create_kwargs,
            )
            context.current_image = None
            context.input_texts = ''
            context.output_texts = ''
            for chunk in completion:
                if stream_key and stream_key not in context.active_stream_keys:
                        cancelled = True
                        try:
                            completion.close()
                        except Exception:
                            pass
                        break
                if (chunk and chunk.choices and chunk.choices[0] and chunk.choices[0].delta.content):
                    output_text = chunk.choices[0].delta.content
                    context.output_texts += output_text
                    logger.info(output_text)
                    output = DataBundle(output_definition)
                    output.set_main_data(output_text)
                    streamer.stream_data(output)
            if not cancelled:
                context.history.add_message(HistoryMessage(role="human", content=chat_text))
                context.history.add_message(HistoryMessage(role="avatar", content=context.output_texts))
        except Exception as e:
            logger.error(e)
            if isinstance(e, APIStatusError):
                response = e.body
                if isinstance(response, dict) and "message" in response:
                    error_text = f"{response['message']}"
                else:
                    error_text = str(response) if response else str(e)
            else:
                # Handle APIConnectionError and other exceptions
                error_text = f"连接错误: {e}"
            output = DataBundle(output_definition)
            output.set_main_data(error_text)
            streamer.stream_data(output, finish_stream=True)
        context.input_texts = ''
        context.output_texts = ''
        if cancelled:
            return
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data('')
        streamer.stream_data(end_output, finish_stream=True)

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        context = cast(LLMContext, context)
        if signal.type == ChatSignalType.STREAM_CANCEL and signal.related_stream:
            stream_key = signal.related_stream.stream_key_str
            if stream_key is not None and stream_key in context.active_stream_keys:
                context.active_stream_keys.discard(stream_key)
                logger.info(f"LLM: Removed stream {stream_key} from active set")

    def destroy_context(self, context: HandlerContext):
        context = cast(LLMContext, context)
        if context.client is not None:
            try:
                context.client.close()
            except Exception:
                pass
            context.client = None

    def _build_bocha_search_context(self, context: LLMContext, query: str) -> str:
        if not self._should_search(context, query):
            return ""
        if not context.bocha_api_key:
            logger.warning("Bocha web search is enabled but BOCHA_API_KEY is not set.")
            return ""
        try:
            results = search_bocha(
                query,
                context.bocha_api_key,
                endpoint=context.bocha_endpoint,
                timeout=context.web_search_timeout,
                result_limit=context.web_search_result_limit,
            )
        except Exception as e:
            logger.warning(f"Bocha web search failed: {e}")
            return ""

        formatted = format_search_results(results)
        if not formatted:
            return ""
        return (
            "以下是实时联网搜索结果。回答用户时优先参考这些结果；"
            "如果搜索结果不足或相互矛盾，请明确说明不确定。"
            "回答中可以简短提及来源。\n\n"
            f"{formatted}"
        )

    def _should_search(self, context: LLMContext, query: str) -> bool:
        if context.web_search_always:
            return True
        trigger_keywords = (
            "搜索",
            "搜一下",
            "帮我搜",
            "查一下",
            "帮我查",
            "查询",
            "联网",
            "最新",
            "最近",
            "今天",
            "现在",
            "新闻",
        )
        return any(keyword in query for keyword in trigger_keywords)

    @staticmethod
    def _extract_music_request(text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        patterns = [
            r"^(?:请)?(?:帮我)?(?:播放|点播|点歌|放一下|放一首|来一首|想听|我要听|我想听|听一下)\s*(.+)$",
            r"^(.+?)(?:这首歌)?(?:播放一下|放一下|来一首)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return match.group(1).strip(" ，。！？!?,;；：:\"'《》")
        if any(keyword in normalized for keyword in ("点歌", "播放音乐", "放音乐")):
            return re.sub(
                r"(点歌|播放音乐|放音乐|播放|帮我|请)",
                "",
                normalized,
            ).strip(" ，。！？!?,;；：:\"'《》")
        return ""

    def _handle_music_request(
        self,
        context: LLMContext,
        music_query: str,
        output_definition,
        output_definitions: Dict[ChatDataType, HandlerDataInfo],
        streamer,
        stream_key: Optional[str],
        original_text: str,
        source_stream_id,
    ):
        reply = ""
        play_url = ""
        song_title = music_query
        artist = ""
        if MusicRequestTool is None:
            reply = "点歌工具暂时不可用，我还没有加载到音乐模块。"
        else:
            try:
                result = MusicRequestTool().execute({"song_name": music_query, "limit": 5})
                if result.success:
                    play_url = result.data.get("play_url") or ""
                    selected = result.data.get("selected") or {}
                    song_title = selected.get("title") or selected.get("name") or music_query
                    artist = selected.get("artist") or ""
                    reply = f"正在播放《{song_title}》" + (f" - {artist}" if artist else "")
                else:
                    reply = f"点歌失败：{result.error}"
            except Exception as e:
                logger.error(f"Music request failed: {e}")
                reply = f"点歌失败：{e}"

        if stream_key:
            context.active_stream_keys.add(stream_key)
        streamed_audio = False
        if play_url:
            try:
                audio_definition = output_definitions.get(ChatDataType.AVATAR_AUDIO).definition
                audio_streamer = context.data_submitter.get_streamer(ChatDataType.AVATAR_AUDIO)
                audio_streamer.new_stream(
                    sources=[source_stream_id] if source_stream_id else [],
                    name="music_request",
                    config=ChatStreamConfig(cancelable=True),
                )
                self._stream_music_audio(play_url, audio_definition, audio_streamer)
                streamed_audio = True
            except Exception as e:
                logger.error(f"Music playback failed: {e}")
                reply = f"已找到《{song_title}》，但播放失败：{e}"

        if not streamed_audio:
            output = DataBundle(output_definition)
            output.set_main_data(reply)
            streamer.stream_data(output)
        context.history.add_message(HistoryMessage(role="human", content=original_text))
        context.history.add_message(HistoryMessage(role="avatar", content=reply))
        context.input_texts = ""
        context.output_texts = ""
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data("")
        streamer.stream_data(end_output, finish_stream=True)

    def _stream_music_audio(self, play_url: str, audio_definition, audio_streamer):
        audio, sample_rate = self._download_music_audio(play_url)
        target_sample_rate = 24000
        if sample_rate != target_sample_rate:
            import librosa
            audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=target_sample_rate)
        audio = audio.astype("float32", copy=False)
        max_seconds = _env_float("OPENAVATAR_MUSIC_MAX_SECONDS", 240.0)
        if max_seconds > 0:
            audio = audio[: int(target_sample_rate * max_seconds)]
        if audio.size == 0:
            raise RuntimeError("音乐音频为空")

        chunk_size = int(target_sample_rate * 0.5)
        for offset in range(0, len(audio), chunk_size):
            chunk = audio[offset: offset + chunk_size]
            if chunk.size == 0:
                continue
            output = DataBundle(audio_definition)
            output.set_main_data(chunk.reshape(1, -1))
            audio_streamer.stream_data(output)

        end_output = DataBundle(audio_definition)
        end_output.set_main_data(__import__("numpy").zeros((1, 240), dtype="float32"))
        audio_streamer.stream_data(end_output, finish_stream=True)

    def _download_music_audio(self, play_url: str):
        import io
        import av
        import numpy as np
        import requests

        headers = {"User-Agent": "OpenAvatarChat/1.0"}
        response = requests.get(play_url, headers=headers, timeout=20)
        response.raise_for_status()
        container = av.open(io.BytesIO(response.content))
        frames = []
        sample_rate = None
        for frame in container.decode(audio=0):
            arr = frame.to_ndarray()
            if arr.ndim == 2:
                if arr.shape[0] <= arr.shape[1]:
                    arr = arr.mean(axis=0)
                else:
                    arr = arr.mean(axis=1)
            if np.issubdtype(arr.dtype, np.integer):
                max_value = float(np.iinfo(arr.dtype).max)
                arr = arr.astype(np.float32) / max_value
            else:
                arr = arr.astype(np.float32)
            frames.append(arr)
            sample_rate = frame.sample_rate
        container.close()
        if not frames or sample_rate is None:
            raise RuntimeError("音乐解码失败")
        audio = np.concatenate(frames)
        audio = np.clip(audio, -1.0, 1.0)
        return audio, sample_rate
