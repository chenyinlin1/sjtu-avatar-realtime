

import os
import re
from typing import Dict, Optional, Set, cast
from loguru import logger
from urllib.parse import urlsplit
from pydantic import BaseModel, Field
from abc import ABC
from openai import APIStatusError, OpenAI
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalSourceType, ChatSignalType
from chat_engine.data_models.chat_stream import StreamKey
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from .chat_history_manager import ChatHistory, HistoryMessage
from .scopemem_adapter import OpenAvatarScopeMemory
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


def _summarize_url_for_log(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except Exception:
        return "<invalid-url>"
    path = parts.path or ""
    if len(path) > 96:
        path = "..." + path[-96:]
    host = parts.netloc or "<no-host>"
    return f"{parts.scheme}://{host}{path}"


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
    enable_scopemem: bool = Field(default=False)
    scopemem_store_path: str = Field(default="runtime/scopemem/memories.jsonl")
    scopemem_user_name: str = Field(default="User")
    scopemem_assistant_name: str = Field(default="Assistant")
    scopemem_top_k: int = Field(default=6)
    scopemem_memory_max_chars: int = Field(default=1600)
    scopemem_extract_batch_size: int = Field(default=8)
    scopemem_clear_on_start: bool = Field(default=False)


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
        self.scopemem = None
        self.enable_video_input = False
        self.active_stream_keys: Set[StreamKey] = set()
        self.web_search_mode = "off"
        self.web_search_always = False
        self.bocha_api_key = None
        self.bocha_endpoint = None
        self.web_search_timeout = 3.0
        self.web_search_result_limit = 5
        self.music_player_active = False


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
        if handler_config.enable_scopemem:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
            store_path = handler_config.scopemem_store_path
            if not os.path.isabs(store_path):
                store_path = os.path.join(project_root, store_path)
            context.scopemem = OpenAvatarScopeMemory(
                client=context.client,
                model_name=context.model_name,
                store_path=store_path,
                user_name=handler_config.scopemem_user_name,
                assistant_name=handler_config.scopemem_assistant_name,
                top_k=handler_config.scopemem_top_k,
                memory_max_chars=handler_config.scopemem_memory_max_chars,
                extract_batch_size=handler_config.scopemem_extract_batch_size,
                clear_on_start=handler_config.scopemem_clear_on_start,
            )
            logger.info(f"ScopeMem memory enabled with store: {store_path}")
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
        music_control = self._extract_music_control(chat_text)
        if music_control:
            logger.info(f"Music control detected: {music_control}")
            self._handle_music_control(
                context,
                music_control,
                output_definition,
                streamer,
                stream_key,
                chat_text,
            )
            return
        if context.music_player_active:
            logger.info(f"Music player active, ignore non-control ASR text: {chat_text}")
            self._finish_empty_response(context, output_definition, streamer, stream_key)
            return
        music_query = self._extract_music_request(chat_text)
        if music_query:
            logger.info(f"Music request detected: {music_query}")
            self._handle_music_request(
                context,
                music_query,
                output_definition,
                streamer,
                stream_key,
                chat_text,
            )
            return
        current_content = context.history.generate_next_messages(chat_text, 
                                                                 [context.current_image] if context.current_image is not None else [])
        logger.debug(f'llm input {context.model_name} {current_content} ')
        if stream_key:
            context.active_stream_keys.add(stream_key)
        cancelled = False
        try:
            system_prompt = context.system_prompt
            if context.scopemem is not None:
                try:
                    system_prompt = context.scopemem.build_system_prompt(context.system_prompt, chat_text)
                except Exception as e:
                    logger.warning(f"ScopeMem memory retrieval failed: {e}")
            messages = [
                system_prompt,
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
                if context.scopemem is not None:
                    context.scopemem.remember_turn(chat_text, context.output_texts)
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
        if context.scopemem is not None:
            try:
                context.scopemem.close()
            except Exception:
                pass
            context.scopemem = None
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

    @staticmethod
    def _extract_music_control(text: str) -> Optional[dict]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        compact = re.sub(r"\s+", "", normalized)
        if any(
            keyword in compact for keyword in ("停止音乐", "结束播放", "关闭音乐", "退出音乐", "别放了", "不听了")
        ):
            return {"action": "stop"}
        if compact in {"暂停", "停"} or any(
            keyword in compact for keyword in ("暂停音乐", "暂停播放", "先暂停", "暂停一下", "停一下")
        ):
            return {"action": "pause"}
        if compact in {"继续", "恢复"} or any(
            keyword in compact for keyword in ("继续播放", "继续音乐", "恢复播放", "接着放", "接着播放")
        ):
            return {"action": "resume"}
        if compact in {"下一首", "下首"} or any(keyword in compact for keyword in ("下一首", "下首歌", "换一首", "切歌")):
            return {"action": "next"}
        if any(keyword in compact for keyword in ("音量小一点", "小声一点", "声音小一点", "降低音量", "调小音量")):
            return {"action": "volume", "delta": -0.15}
        if any(keyword in compact for keyword in ("音量大一点", "大声一点", "声音大一点", "提高音量", "调大音量")):
            return {"action": "volume", "delta": 0.15}
        if any(keyword in compact for keyword in ("静音", "关闭音乐声音")):
            return {"action": "mute"}
        if any(keyword in compact for keyword in ("取消静音", "打开音乐声音")):
            return {"action": "unmute"}
        return None

    def _handle_music_request(
        self,
        context: LLMContext,
        music_query: str,
        output_definition,
        streamer,
        stream_key: Optional[str],
        original_text: str,
    ):
        reply = ""
        play_url = ""
        song_title = music_query
        artist = ""
        source = ""
        candidates = []
        if MusicRequestTool is None:
            logger.warning(f"Music tool unavailable: query={music_query!r}")
            reply = "点歌工具暂时不可用，我还没有加载到音乐模块。"
        else:
            try:
                result = MusicRequestTool().execute({"song_name": music_query, "limit": 5})
                if result.success:
                    play_url = result.data.get("play_url") or ""
                    selected = result.data.get("selected") or {}
                    song_title = selected.get("title") or selected.get("name") or music_query
                    artist = selected.get("artist") or ""
                    source = result.data.get("source") or ""
                    candidates = result.data.get("candidates") or []
                    reply = f"正在播放《{song_title}》" + (f" - {artist}" if artist else "")
                    logger.info(
                        f"Music tool result: success=True query={music_query!r} "
                        f"source={source or '-'} title={song_title!r} artist={artist!r} "
                        f"candidates={len(candidates)} play_url_present={bool(play_url)} "
                        f"play_url={_summarize_url_for_log(play_url)}"
                    )
                else:
                    reply = f"点歌失败：{result.error}"
                    details = (result.data or {}).get("details") if result.data else None
                    logger.warning(
                        f"Music tool result: success=False query={music_query!r} "
                        f"error={result.error} details_count={len(details or [])}"
                    )
                    if details:
                        logger.info(f"Music tool failure details sample: {details[:3]}")
            except Exception as e:
                logger.exception(f"Music request failed: query={music_query!r} error={e}")
                reply = f"点歌失败：{e}"

        if stream_key:
            context.active_stream_keys.add(stream_key)
        if play_url:
            context.music_player_active = True
            output = DataBundle(output_definition)
            output.set_main_data("")
            output.add_meta("client_action", {
                "type": "music.play",
                "title": song_title,
                "artist": artist,
                "url": play_url,
                "source": source,
                "query": music_query,
                "candidates": candidates,
                "hints": ["暂停", "继续", "下一首", "音量小一点"],
            })
            streamer.stream_data(output)
            logger.info(
                f"Music client_action dispatch: type=music.play stream_key={stream_key} "
                f"title={song_title!r} artist={artist!r} source={source or '-'} "
                f"url={_summarize_url_for_log(play_url)}"
            )
        else:
            output = DataBundle(output_definition)
            output.set_main_data(reply)
            streamer.stream_data(output)
            logger.info(
                f"Music request completed without playable URL: query={music_query!r} reply={reply!r}"
            )
        context.history.add_message(HistoryMessage(role="human", content=original_text))
        context.history.add_message(HistoryMessage(role="avatar", content=reply))
        context.input_texts = ""
        context.output_texts = ""
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data("")
        streamer.stream_data(end_output, finish_stream=True)

    def _handle_music_control(
        self,
        context: LLMContext,
        control: dict,
        output_definition,
        streamer,
        stream_key: Optional[str],
        original_text: str,
    ):
        if stream_key:
            context.active_stream_keys.add(stream_key)
        action = control.get("action")
        if action == "stop":
            context.music_player_active = False
        elif action in {"pause", "resume", "next", "volume", "mute", "unmute"}:
            context.music_player_active = True
        output = DataBundle(output_definition)
        output.set_main_data("")
        output.add_meta("client_action", {
            "type": "music.control",
            "action": action,
            "delta": control.get("delta"),
            "hints": ["暂停", "继续", "下一首", "音量小一点"],
        })
        streamer.stream_data(output)
        logger.info(
            f"Music client_action dispatch: type=music.control stream_key={stream_key} "
            f"action={action} delta={control.get('delta')} active={context.music_player_active}"
        )
        if action == "stop":
            context.emit_signal(
                ChatSignal(
                    type=ChatSignalType.INTERRUPT,
                    source_type=ChatSignalSourceType.HANDLER,
                    source_name=context.owner or "LLMOpenAICompatible",
                    signal_data={
                        "reason": "music_stop",
                        "trigger_text": original_text[:100],
                    },
                )
            )
            logger.info("Music stop emitted interrupt to clear pending avatar response streams")
        context.history.add_message(HistoryMessage(role="human", content=original_text))
        context.history.add_message(HistoryMessage(role="avatar", content=f"music.control:{action}"))
        context.input_texts = ""
        context.output_texts = ""
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data("")
        streamer.stream_data(end_output, finish_stream=True)

    def _finish_empty_response(
        self,
        context: LLMContext,
        output_definition,
        streamer,
        stream_key: Optional[str],
    ):
        context.input_texts = ""
        context.output_texts = ""
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data("")
        streamer.stream_data(end_output, finish_stream=True)
