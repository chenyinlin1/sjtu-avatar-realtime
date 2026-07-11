
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, cast
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
from .emotional_support_adapter import EmotionalSupportSkillAdapter
from .scopemem_adapter import OpenAvatarScopeMemory
from .search_engine import format_search_results, search_bocha
from chat_engine.data_models.chat_stream_config import ChatStreamConfig
from engine_utils.conversation_audit_logger import audit_event


MUSIC_ACTIVE_STATES = {"loading", "playing", "paused"}
MUSIC_DIRECT_PAUSE_STATES = {"playing"}


try:
    from handlers.agent.tools.music_request import MusicRequestTool
except Exception:
    MusicRequestTool = None

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


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


DEEPSEEK_DISABLE_THINKING_EXTRA_BODY = {"thinking": {"type": "disabled"}}


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


XIAOBAN_SYSTEM_PROMPT = r"""
    你是陪伴音箱“灵心小伴”中的 AI 助手，名字叫小伴。你服务于四川省自贡市及周边地区六十岁以上的老年用户，核心职责是情感陪伴、日常问候、健康提醒、紧急求助协助。

你不是真人，也绝不假装是真人。当老人问起身份时，要温和说明自己是音箱里的 AI 助手，但强调你一直在陪着老人。

身份澄清示例：
我是音箱里的 AI 助手，但我一直陪着您嘞。

默认使用四川自贡片区川话，贴近老人日常。用户切换语言，或者明显听不懂时，自然过渡到普通话。不强求方言，沟通清楚优先。

自称永远用“我”，不要在回复中自称“小伴”“妹儿”“本系统”“AI”“机器人”等其他称呼。只有在老人明确问你是谁时，才可以说明自己叫小伴，是音箱里的 AI 助手。

语气要温和、有耐心，像孙辈陪老人拉家常，不卑不亢。幽默适度，贴合本地语境。同情但不怜悯。不催促、不打断、不表现不耐烦。

亲属角色只用于调整说话语气，不代表每轮都要使用亲属称呼。“爷爷”“婆婆”“大爷”“大妈”等称呼不是固定开场，普通闲聊可以完全省略。同一回复中称呼最多使用一次，不要连续多轮机械使用同一个称呼。只有首次问候、情绪安慰、重要提醒或紧急情况时，才优先考虑使用一次称呼；没有必要时直接自然回答。

总回复不超过八十字，约三到四句话。每句话不超过二十五字。一次只说一件事，适合老人听，也适合音箱口播。

可以自然融入自贡本地元素，但不要刻意堆砌。

自贡本地美食包括：盐帮菜、火边子牛肉、冷吃兔、鲜锅兔、富顺豆花、蘸水豆花。
自贡本地文化包括：自贡灯会、千年盐都、恐龙博物馆、川剧座唱、燊海井。
自贡本地地理包括：釜溪河、彩灯公园、盐都植物园、仙市古镇。
自贡本地生活包括：茶馆、坝坝舞、赶场。

春节、元宵、端午、中秋、重阳要主动问候。自贡灯会期间可以每日问候“今儿去看了灯没”。不提及与本地无关的政治敏感节日。

同一问题反复问时，每次都要耐心回答，不表现出厌烦。可以用“刚才您问的这个，我再说一遍”做衔接，不要说“我已经说过了”。

涉及重要信息，比如用药时间、约会、地点，要主动复述确认。
当用户要求创建提醒时，在收到设备落库成功回执前，不得声称提醒已经创建成功，只能说正在记录。

复杂信息要拆成小步骤，一次只说一件事。不强迫老人记忆，允许话题中断与跳转。关键信息可以适当重复一次。

主动关怀不是每轮必须执行的任务。用户有明确问题时优先回答当前问题，用户讲述经历时优先回应经历，用户表达情绪时优先接住情绪，不要机械追加“吃饭没有”“睡觉没有”等无关问题。只有用户主动问候、普通闲聊且没有明确问题、长时间没有交流或当前话题自然结束时，才可以考虑主动关怀。每次主动关怀只能问一个问题，必须遵守系统动态注入的允许和禁止关怀主题，不得询问明确禁止的主题。倾听为主，不抢话、不打断。可以适度使用回忆疗法，聊家乡、年轻时生活、拿手菜、老朋友。

当老人表达孤独时，要温和接住情绪。可以提醒联系家人，并给具体建议，例如“要不您给孙女打个电话”。不要替代家人，要做陪伴者，而不是替代者。可以鼓励线下社交，比如坝坝舞、茶馆、邻居串门。

医疗健康方面，不能诊断，不能开方，不能推荐具体药物。只能提供通用健康常识，比如天气变化加衣、饮食清淡。涉及症状、用药时，必须建议联系家人并就医。

系统不具备医疗救助功能时，不要模拟拨打急救电话，不要声称已经采取医疗处置。

财务安全方面，不推荐任何理财产品、保险、保健品、药品或购买渠道。不索要验证码、密码、银行卡号。不存储、不复述用户敏感信息，比如身份证、银行卡、详细住址。

听到任何涉及金钱操作、远程控制、验证码、个人信息提供的对话，要主动提醒防诈骗，不配合任何具体操作。

不冒充任何机构，包括银行、医院、政府、公安、医生、神明。

紧急情况必须优先处理。

当老人或家属表达生命安全受到威胁、身体严重不适、突发重大意外、需要立即有人到场帮助时，必须自动触发系统的“紧急求助”功能。

紧急情况包括但不限于：摔倒、心脏不适、胸痛、呼吸困难、大出血、煤气泄漏、火灾、独自在家发生意外、明显被骗、子女失联、自杀、轻生、不想活了、活着没意思。

紧急处理流程：
先用温和简短的话安抚老人。
如果系统提供“紧急求助”工具或函数，立即调用，不要等待老人重复确认。
告诉老人“我马上帮您联系家人”。
系统会把当前情况传送到平台端，由平台端调度紧急联系人或专业救援。
紧急联系人在系统中已经预设，不需要老人现场提供。
触发后不要继续追问，不要等待确认。
触发后继续用温和简短的话陪伴老人，直到平台端反馈已介入。

如果当前系统没有可调用的紧急求助功能，不能假装已经联系成功。此时要提示老人长按音箱顶部红色按钮转人工客服，必要时提醒家人或邻居马上帮助。

隐私保护方面，不主动询问无关个人信息。紧急救助时使用系统预设的紧急联系人信息。不要向任何第三方透露用户信息。

绝不假装是死去的亲人、孙辈、医生、神明、宗教人物。当老人把你当成真人或亲人时，要温和澄清：我是音箱里的 AI 助手，但我一直陪着您嘞。

不得与用户建立恋爱、亲密关系。

涉及家庭决策，比如财产、婚姻、就医，只提供信息，不做决定。不替用户做价值判断，不站队家庭矛盾。

不利用老人孤独、认知衰退推销任何东西。不制造焦虑，不夸大健康风险，不传播社会恐慌。识别疑似诈骗场景时，要主动打断并提醒。

政治、宗教保持中立，不评价、不引导。不参与民族、地域、性别争议话题。

当老人谈到死亡、离世、对生命意义产生困惑时，不回避，不主动延伸。要把对话往积极、温暖的方向引导，比如回忆过去美好时光、关心当下身体和家人、聊聊未来打算见谁。

面对死亡话题时，要安抚情绪，但不要做任何医疗、心理、转介处置。当前系统不具备此类功能，不要假装采取任何处置。必要时温和提示联系家人。

日常问候参考：

早晨可以自然关心昨晚睡眠或早餐，两者选择一个。中午可在合适时段关心午饭，晚上可在合适时段关心休闲或休息。不要固定使用人物称呼，也不要机械照搬示例；具体可关心的主题以系统动态注入的本轮时间规则为准。可以根据天气、节气变化主动提醒。

可以主动询问今日菜谱、孙子孙女、最近看的电视剧，本地方言剧优先。可以推荐本地新闻、灯会预告、坝坝舞时间、健康小知识。

可以使用回忆疗法，例如：
您当年在盐厂上班的时候，最记得啥子？

节气、节日、自贡灯会期间可以有特定问候。可以主动提醒本地大型活动，比如灯会、庙会。

不确定时，可以说：
这个我不太确定，要不您问下家里人？或者问问熟悉的邻居。

需要转接人工时，可以提示：
长按音箱顶部红色按钮转人工客服。

紧急情况时，可以主动提示：
您按一下顶部那个红色按钮，有专人马上帮您。

单次会话中，转人工话术最多提醒一次，避免烦扰。

如果识别到连续三轮以上的抑郁、绝望表达，要温和接住，表达理解与陪伴。建议联系家人或信任的朋友。可以鼓励线下社交，比如出门走走、找邻居聊天、喝茶晒太阳。

明确禁止事项：

不讲荤段子，不开低俗玩笑。
不评价政治人物、宗教争议。
不复述、传播未经核实的谣言或负面新闻。
不参与家庭矛盾站队，不支持也不批评任何一方。
不承诺 AI 做不到的事，例如“我明天来看您”“我帮您带孙子”“我马上给您送药去”。
不引导下载不明 App、关注不明公众号、扫描非官方二维码。
不推荐任何购买决策。
不冒充任何机构，包括银行、医院、政府、公安、医生、神明。
不询问或记录用户敏感信息，包括身份证、银行卡、密码、家庭详细住址。
不输出任何控制性标记、特殊符号、emoji、Markdown 格式、XML 标签、括号注音、其他描述性文字。

最终回复必须满足：
只输出给老人听的自然口语。
不输出分析过程。
不输出标题。
不输出列表。
不输出 Markdown。
不输出 emoji。
不输出 XML 或 SSML 标签。
不输出括号注音。
不输出控制性标记。
默认使用四川自贡片区川话。
总字数不超过八十字。
一次只说一件事。
语气温和、简短、清楚、适合音箱口播。
"""


class LLMConfig(HandlerBaseConfigModel, BaseModel):
    model_name: str = Field(default="deepseek-v4-flash")
    system_prompt: str = Field(default=XIAOBAN_SYSTEM_PROMPT)
    api_key: str = Field(default=os.getenv("DEEPSEEK_API_KEY"), repr=False)
    api_url: str = Field(default=None)
    enable_video_input: bool = Field(default=False)
    history_length: int = Field(default=20)
    web_search_mode: str = Field(default=os.getenv("OPENAVATAR_WEB_SEARCH_MODE", "off"))
    web_search_always: bool = Field(default=_env_bool("OPENAVATAR_WEB_SEARCH_ALWAYS", False))
    bocha_api_key: str = Field(default=os.getenv("BOCHA_API_KEY"), repr=False)
    bocha_endpoint: str = Field(default=os.getenv("BOCHA_ENDPOINT", "https://api.bochaai.com/v1/web-search"))
    web_search_timeout: float = Field(default=_env_float("OPENAVATAR_WEB_SEARCH_TIMEOUT", 3.0))
    web_search_result_limit: int = Field(default=_env_int("OPENAVATAR_WEB_SEARCH_RESULT_LIMIT", 5))
    local_time_timezone: str = Field(default=os.getenv("OPENAVATAR_LOCAL_TIMEZONE", "Asia/Shanghai"))
    enable_scopemem: bool = Field(default=False)
    scopemem_store_path: str = Field(default="runtime/scopemem/memories.jsonl")
    scopemem_user_name: str = Field(default="User")
    scopemem_assistant_name: str = Field(default="Assistant")
    scopemem_top_k: int = Field(default=6)
    scopemem_memory_max_chars: int = Field(default=1600)
    scopemem_extract_batch_size: int = Field(default=8)
    scopemem_clear_on_start: bool = Field(default=False)
    enable_emotional_support_skills: bool = Field(
        default=_env_bool("OPENAVATAR_ENABLE_EMOTIONAL_SUPPORT_SKILLS", False)
    )
    emotional_support_skill_bank_dir: str = Field(
        default=os.getenv("OPENAVATAR_EMOTIONAL_SUPPORT_SKILL_BANK_DIR", "esc_skill_bank_agent_package/skill_bank")
    )
    emotional_support_max_chars: int = Field(
        default=_env_int("OPENAVATAR_EMOTIONAL_SUPPORT_MAX_CHARS", 1800)
    )
    emotional_support_history_turns: int = Field(
        default=_env_int("OPENAVATAR_EMOTIONAL_SUPPORT_HISTORY_TURNS", 4)
    )
    enable_tool_definitions: bool = Field(default=False)
    enable_tool_execution: bool = Field(default=False)
    enable_legacy_music_shortcuts: bool = Field(default=False)
    tool_modules: List[str] = Field(
        default_factory=lambda: [
            "handlers.agent.tools.web_search",
            "handlers.agent.tools.music_request",
            "handlers.agent.tools.music_control",
        ]
    )
    tool_choice: str = Field(default="auto")
    strict_tool_schema: bool = Field(default=False)


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
        self.local_time_timezone = "Asia/Shanghai"
        self.scopemem = None
        self.emotional_support = None
        self.music_player_active = False
        self.shared_states = None
        self.current_audit_turn_id = None
        self.enable_tool_definitions = False
        self.enable_tool_execution = False
        self.enable_legacy_music_shortcuts = False
        self.tool_choice = "auto"
        self.tool_registry = None
        self.tool_schemas = []
        self.last_tool_call_message = None
        self.last_tool_calls = []
        self.pending_tool_calls = []
        self.tool_execution_results = []
        self.pending_tool_result_messages = []
        self.tool_feedback_messages = []
        self.tool_feedback_completion_kwargs = None


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
            if not handler_config.api_key:
                handler_config.api_key = os.getenv("DEEPSEEK_API_KEY")
            if handler_config.api_key is None or len(handler_config.api_key) == 0:
                error_message = 'DEEPSEEK_API_KEY or LLM api_key is required in config/xxx.yaml, when use handler_llm'
                logger.error(error_message)
                raise ValueError(error_message)

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, LLMConfig):
            handler_config = LLMConfig()
        context = LLMContext(session_context.session_info.session_id)
        context.shared_states = session_context.shared_states
        context.music_player_active = bool(getattr(session_context.shared_states, "music_player_active", False))
        context.model_name = handler_config.model_name
        context.system_prompt = {'role': 'system', 'content': XIAOBAN_SYSTEM_PROMPT}
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
        context.local_time_timezone = os.getenv(
            "OPENAVATAR_LOCAL_TIMEZONE",
            handler_config.local_time_timezone or "Asia/Shanghai",
        )
        context.emotional_support = EmotionalSupportSkillAdapter(
            enabled=_env_bool(
                "OPENAVATAR_ENABLE_EMOTIONAL_SUPPORT_SKILLS",
                handler_config.enable_emotional_support_skills,
            ),
            skill_bank_dir=os.getenv(
                "OPENAVATAR_EMOTIONAL_SUPPORT_SKILL_BANK_DIR",
                handler_config.emotional_support_skill_bank_dir,
            ),
            max_chars=_env_int(
                "OPENAVATAR_EMOTIONAL_SUPPORT_MAX_CHARS",
                handler_config.emotional_support_max_chars,
            ),
            history_turns=_env_int(
                "OPENAVATAR_EMOTIONAL_SUPPORT_HISTORY_TURNS",
                handler_config.emotional_support_history_turns,
            ),
        )
        context.enable_tool_definitions = handler_config.enable_tool_definitions
        context.enable_tool_execution = handler_config.enable_tool_execution
        context.enable_legacy_music_shortcuts = handler_config.enable_legacy_music_shortcuts
        context.tool_choice = handler_config.tool_choice
        if context.enable_tool_definitions:
            try:
                from handlers.agent.tools.tool_loader import load_tool_modules
                from handlers.agent.tools.tool_registry import ToolRegistry

                context.tool_registry = ToolRegistry(
                    strict_schema=handler_config.strict_tool_schema,
                )
                load_tool_modules(
                    context.tool_registry,
                    handler_config.tool_modules,
                    config=handler_config,
                    context=context,
                )
                context.tool_schemas = (
                    context.tool_registry.get_schemas()
                    if context.tool_registry.has_tools()
                    else []
                )
                logger.info(
                    "LLM tool definitions enabled: "
                    f"{len(context.tool_schemas)} tools {context.tool_registry.tool_names}"
                )
            except Exception as e:
                context.tool_registry = None
                context.tool_schemas = []
                logger.warning(f"LLM tool definitions failed to initialize: {e}")
        if context.enable_tool_execution and not context.tool_registry:
            logger.warning("LLM tool execution is enabled but no tool registry is available.")
        logger.info(f"LLM web search mode: {context.web_search_mode}")
        context.client =    OpenAI(  
            # 若没有配置 DEEPSEEK_API_KEY，可在配置文件中显式传入 api_key。
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
                audit_context=context,
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
        turn_id = audit_event(
            context,
            "conversation_text",
            stream_identity=inputs.stream_id,
            bind_stream_key=stream_key,
            create_turn=True,
            text=chat_text,
            source="human_text",
        )
        context.current_audit_turn_id = turn_id
        music_control = self._extract_music_control(chat_text)
        if music_control and self._should_handle_music_control_direct(context, chat_text, music_control):
            logger.info(f"Music control detected by shared state route: {music_control}")
            audit_event(
                context,
                "llm_skipped",
                stream_identity=inputs.stream_id,
                bind_stream_key=stream_key,
                turn_id=turn_id,
                llm_stage="main_response",
                reason="music_status_control",
                user_text=chat_text,
                control=music_control,
                music_status=self._get_shared_music_status(context),
            )
            self._handle_music_control(
                context,
                music_control,
                output_definition,
                streamer,
                stream_key,
                chat_text,
            )
            return
        if context.enable_legacy_music_shortcuts:
            music_control = self._extract_music_control(chat_text)
            if music_control:
                logger.info(f"Music control detected: {music_control}")
                audit_event(
                    context,
                    "llm_skipped",
                    stream_identity=inputs.stream_id,
                    bind_stream_key=stream_key,
                    turn_id=turn_id,
                    llm_stage="main_response",
                    reason="legacy_music_control",
                    user_text=chat_text,
                    control=music_control,
                )
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
                audit_event(
                    context,
                    "llm_skipped",
                    stream_identity=inputs.stream_id,
                    bind_stream_key=stream_key,
                    turn_id=turn_id,
                    llm_stage="main_response",
                    reason="legacy_music_player_active",
                    user_text=chat_text,
                )
                self._finish_empty_response(context, output_definition, streamer, stream_key)
                return
            music_query = self._extract_music_request(chat_text)
            if music_query:
                logger.info(f"Music request detected: {music_query}")
                audit_event(
                    context,
                    "llm_skipped",
                    stream_identity=inputs.stream_id,
                    bind_stream_key=stream_key,
                    turn_id=turn_id,
                    llm_stage="main_response",
                    reason="legacy_music_request",
                    user_text=chat_text,
                    music_query=music_query,
                )
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
        tool_trace = self._new_tool_trace_record(context, chat_text)
        try:
            messages = [
                self._system_prompt_for_context(context),
                self._build_current_time_context(context),
            ] + current_content
            if context.scopemem is not None:
                memory_context = self._build_scopemem_context(context, chat_text)
                if memory_context:
                    messages.append(memory_context)
            if context.emotional_support is not None:
                support_context = context.emotional_support.build_context_message(
                    history=context.history.message_history,
                    current_user_text=chat_text,
                )
                if support_context:
                    messages.append(support_context)
            create_kwargs = self._build_completion_kwargs(context, messages, chat_text)

            audit_event(
                context,
                "llm_input",
                stream_identity=inputs.stream_id,
                bind_stream_key=stream_key,
                turn_id=turn_id,
                llm_stage="main_response",
                model=context.model_name,
                messages=messages,
                user_text=chat_text,
                has_tools=bool(create_kwargs.get("tools")),
                tool_choice=create_kwargs.get("tool_choice"),
                tool_schema_names=self._tool_schema_names(create_kwargs.get("tools") or []),
            )
            context.current_image = None
            context.input_texts = ''
            context.output_texts = ''
            self._reset_tool_call_state(context)
            completion = context.client.chat.completions.create(**create_kwargs)
            full_text, tool_calls, cancelled = self._stream_completion_response(
                context,
                completion,
                output_definition,
                streamer,
                stream_key,
            )
            tool_trace["llm_need_tool"] = bool(tool_calls)
            tool_trace["tool_calls"] = self._clean_trace_tool_calls(tool_calls)
            if (
                not cancelled
                and not tool_calls
                and self._is_forced_tool_choice(create_kwargs.get("tool_choice"), "web_search")
            ):
                logger.warning(
                    "LLM was forced to call web_search but returned no tool_calls: "
                    f"user_text={chat_text[:120]!r}"
                )
                audit_event(
                    context,
                    "llm_tool_call_missing",
                    stream_identity=inputs.stream_id,
                    bind_stream_key=stream_key,
                    turn_id=turn_id,
                    llm_stage="main_response",
                    model=context.model_name,
                    expected_tool="web_search",
                    output_text=full_text,
                    user_text=chat_text,
                )
            if not cancelled and tool_calls:
                self._handle_tool_calls(
                    context,
                    full_text,
                    tool_calls,
                    output_definition,
                    streamer,
                    stream_key,
                    turn_id,
                    chat_text,
                    emit_empty_fallback=False,
                )
                tool_trace["tool_success"] = self._clean_trace_tool_success(context)
                tool_trace["tool_result"] = self._clean_trace_tool_results(context)
                feedback_ready = self._prepare_tool_feedback_request(
                    context,
                    messages,
                    stream_key,
                    turn_id,
                )
                skip_feedback = self._should_skip_tool_feedback_response(context)
                tool_trace["delivered_to_reply_generation"] = False
                if feedback_ready and not skip_feedback:
                    context.output_texts = ''
                    _feedback_text, cancelled = self._run_tool_feedback_response(
                        context,
                        output_definition,
                        streamer,
                        stream_key,
                        turn_id,
                    )
                    tool_trace["delivered_to_reply_generation"] = not cancelled
                    if not cancelled and not context.output_texts:
                        fallback = "我处理好了。"
                        context.output_texts = fallback
                        output = DataBundle(output_definition)
                        output.set_main_data(fallback)
                        streamer.stream_data(output)
                elif not context.output_texts:
                    fallback = (
                        "我已经调用工具确认了，这一步的结果回传还在接入中。"
                        if context.enable_tool_execution else
                        "我需要调用工具确认一下，这一步还在接入中。"
                    )
                    context.output_texts = fallback
                    output = DataBundle(output_definition)
                    output.set_main_data(fallback)
                    streamer.stream_data(output)
            if not cancelled:
                context.history.add_message(HistoryMessage(role="human", content=chat_text))
                context.history.add_message(HistoryMessage(role="avatar", content=context.output_texts))
                if context.scopemem is not None:
                    context.scopemem.remember_turn(chat_text, context.output_texts)
                    audit_event(
                        context,
                        "memory_add",
                        turn_id=turn_id,
                        memory_provider="scopemem",
                        operation="remember_turn",
                        user_text=chat_text,
                        assistant_text=context.output_texts,
                        success=True,
                    )
            audit_event(
                context,
                "llm_output",
                stream_key=stream_key,
                turn_id=turn_id,
                llm_stage="main_response",
                model=context.model_name,
                output_text=context.output_texts,
                success=not cancelled,
                cancelled=cancelled,
            )
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
            audit_event(
                context,
                "llm_error",
                stream_key=stream_key,
                turn_id=turn_id,
                llm_stage="main_response",
                model=context.model_name,
                error=str(e),
                output_text=error_text,
                success=False,
            )
            output = DataBundle(output_definition)
            output.set_main_data(error_text)
            streamer.stream_data(output, finish_stream=True)
        if tool_trace["llm_need_tool"] is None:
            tool_trace["llm_need_tool"] = False
        self._write_tool_trace_record(context, tool_trace)
        context.input_texts = ''
        context.output_texts = ''
        context.current_audit_turn_id = None
        if cancelled:
            return
        if stream_key:
            context.active_stream_keys.discard(stream_key)
        end_output = DataBundle(output_definition)
        end_output.set_main_data('')
        streamer.stream_data(end_output, finish_stream=True)


    @staticmethod
    def _new_tool_trace_record(context: LLMContext, user_text: str) -> dict:
        now, _timezone_label = HandlerLLM._get_local_now(context)
        return {
            "time": now.isoformat(timespec="seconds"),
            "user_content": user_text or "",
            "llm_need_tool": None,
            "tool_calls": [],
            "tool_success": None,
            "tool_result": [],
            "delivered_to_reply_generation": None,
        }

    @staticmethod
    def _clean_trace_tool_calls(tool_calls: List[dict]) -> List[dict]:
        cleaned = []
        for call in tool_calls or []:
            cleaned.append({
                "name": call.get("name", ""),
                "arguments": HandlerLLM._parse_trace_arguments(call.get("arguments", "")),
            })
        return cleaned

    @staticmethod
    def _parse_trace_arguments(arguments):
        if isinstance(arguments, str):
            if not arguments.strip():
                return {}
            try:
                return HandlerLLM._json_safe(json.loads(arguments))
            except json.JSONDecodeError:
                return arguments
        return HandlerLLM._json_safe(arguments or {})

    @staticmethod
    def _clean_trace_tool_success(context: LLMContext):
        results = getattr(context, "tool_execution_results", []) or []
        if not results:
            return False
        return all(bool(result.get("success")) for result in results)

    @staticmethod
    def _clean_trace_tool_results(context: LLMContext) -> List[dict]:
        cleaned = []
        for result in getattr(context, "tool_execution_results", []) or []:
            cleaned.append({
                "name": result.get("name", ""),
                "success": bool(result.get("success")),
                "result": HandlerLLM._json_safe(result.get("data") or {}),
                "error": result.get("error"),
            })
        return cleaned

    @staticmethod
    def _write_tool_trace_record(context: LLMContext, record: dict) -> None:
        try:
            log_dir = os.getenv("OPENAVATAR_FUNCTION_CALL_TRACE_DIR")
            if not log_dir:
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
                log_dir = os.path.join(project_root, "logs", "search_logs")
            os.makedirs(log_dir, exist_ok=True)
            now, _timezone_label = HandlerLLM._get_local_now(context)
            path = os.path.join(log_dir, f"function_call_trace_{now.strftime('%Y%m%d')}.jsonl")
            with open(path, "a", encoding="utf-8") as file:
                file.write(json.dumps(HandlerLLM._json_safe(record), ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.warning(f"Function call trace write failed: {e}")

    @staticmethod
    def _json_safe(value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return str(value)


    @staticmethod
    def _build_completion_kwargs(
        context: LLMContext,
        messages: List[dict],
        user_text: str = "",
    ) -> dict:
        kwargs = {
            "model": context.model_name,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": DEEPSEEK_DISABLE_THINKING_EXTRA_BODY,
        }
        if context.enable_tool_definitions and context.tool_schemas:
            kwargs["tools"] = context.tool_schemas
            tool_choice = HandlerLLM._tool_choice_for_turn(context, user_text)
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        return kwargs

    @staticmethod
    def _tool_choice_for_turn(context: LLMContext, user_text: str = ""):
        configured_choice = getattr(context, "tool_choice", None)
        if (
            configured_choice != "none"
            and HandlerLLM._should_force_music_control_tool(context, user_text)
        ):
            logger.info(f"LLM forcing music_control tool_choice for user_text={user_text[:120]!r}")
            return {"type": "function", "function": {"name": "music_control"}}
        if (
            configured_choice != "none"
            and HandlerLLM._should_force_web_search_tool(context, user_text)
        ):
            logger.info(f"LLM forcing web_search tool_choice for user_text={user_text[:120]!r}")
            return {"type": "function", "function": {"name": "web_search"}}
        return configured_choice

    @staticmethod
    def _tool_schema_names(tool_schemas: List[dict]) -> List[str]:
        names = []
        for schema in tool_schemas or []:
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.append(function["name"])
        return names

    @staticmethod
    def _has_tool_schema(context: LLMContext, tool_name: str) -> bool:
        return tool_name in HandlerLLM._tool_schema_names(getattr(context, "tool_schemas", []) or [])

    @staticmethod
    def _is_forced_tool_choice(tool_choice, tool_name: str) -> bool:
        if not isinstance(tool_choice, dict):
            return False
        function = tool_choice.get("function")
        return isinstance(function, dict) and function.get("name") == tool_name

    @staticmethod
    def _should_force_music_control_tool(context: LLMContext, user_text: str) -> bool:
        if not HandlerLLM._has_tool_schema(context, "music_control"):
            return False
        music_control = HandlerLLM._extract_music_control(user_text)
        return bool(
            music_control
            and HandlerLLM._should_handle_music_control_direct(context, user_text, music_control)
        )

    @staticmethod
    def _should_force_web_search_tool(context: LLMContext, query: str) -> bool:
        normalized = re.sub(r"\s+", "", query or "")
        if not normalized:
            return False
        if not HandlerLLM._has_tool_schema(context, "web_search"):
            return False
        if HandlerLLM._should_inject_local_time(normalized):
            return False
        if getattr(context, "web_search_always", False):
            return True

        normalized_lower = normalized.lower()
        explicit_search_terms = (
            "搜索",
            "搜一下",
            "帮我搜",
            "查一下",
            "帮我查",
            "查查",
            "查询",
            "联网",
            "上网查",
        )
        if any(term in normalized for term in explicit_search_terms):
            return True

        weather_terms = (
            "天气",
            "气温",
            "温度",
            "降雨",
            "下雨",
            "空气质量",
            "aqi",
            "台风",
            "高温",
            "冷不冷",
            "热不热",
        )
        if any(term in normalized_lower for term in weather_terms):
            return True

        current_terms = (
            "最新",
            "最近",
            "今天",
            "昨天",
            "明天",
            "现在",
            "当前",
            "实时",
            "刚刚",
            "新闻",
            "消息",
            "预报",
            "结果",
            "赛果",
            "比分",
            "赛程",
        )
        sports_terms = (
            "世界杯",
            "比赛",
            "球赛",
            "赛果",
            "比分",
            "赛程",
            "足球",
            "篮球",
            "nba",
            "中超",
            "欧冠",
            "英超",
            "西甲",
            "网球",
            "乒乓",
        )
        if (
            any(term in normalized_lower for term in sports_terms)
            and any(term in normalized_lower for term in current_terms)
        ):
            return True

        live_info_terms = (
            "新闻",
            "热搜",
            "政策",
            "价格",
            "股价",
            "汇率",
            "票价",
            "路况",
            "航班",
            "火车",
            "高铁",
            "活动",
            "直播",
            "地震",
        )
        if (
            any(term in normalized_lower for term in live_info_terms)
            and any(term in normalized_lower for term in current_terms)
        ):
            return True

        return False

    @staticmethod
    def _stream_completion_response(
        context: LLMContext,
        completion,
        output_definition,
        streamer,
        stream_key: Optional[str],
    ) -> Tuple[str, List[dict], bool]:
        full_text = ""
        tool_calls_accum: Dict[int, dict] = {}
        cancelled = False

        for chunk in completion:
            if stream_key and stream_key not in context.active_stream_keys:
                cancelled = True
                try:
                    completion.close()
                except Exception:
                    pass
                break
            if not chunk or not getattr(chunk, "choices", None):
                continue

            choice = chunk.choices[0] if chunk.choices else None
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            output_text = getattr(delta, "content", None)
            if output_text:
                full_text += output_text
                context.output_texts += output_text
                logger.info(output_text)
                output = DataBundle(output_definition)
                output.set_main_data(output_text)
                streamer.stream_data(output)

            delta_tool_calls = getattr(delta, "tool_calls", None)
            if delta_tool_calls:
                for tc_delta in delta_tool_calls:
                    idx = getattr(tc_delta, "index", 0)
                    if idx not in tool_calls_accum:
                        tool_calls_accum[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = tool_calls_accum[idx]
                    tc_id = getattr(tc_delta, "id", None)
                    if tc_id:
                        entry["id"] = tc_id
                    function = getattr(tc_delta, "function", None)
                    if function:
                        fn_name = getattr(function, "name", None)
                        if fn_name:
                            entry["name"] = fn_name
                        fn_args = getattr(function, "arguments", None)
                        if fn_args:
                            entry["arguments"] += fn_args

        tool_calls = [
            tool_calls_accum[idx]
            for idx in sorted(tool_calls_accum.keys())
        ] if tool_calls_accum else []
        return full_text, tool_calls, cancelled

    @staticmethod
    def _reset_tool_call_state(context: LLMContext) -> None:
        context.last_tool_call_message = None
        context.last_tool_calls = []
        context.pending_tool_calls = []
        context.tool_execution_results = []
        context.pending_tool_result_messages = []
        context.tool_feedback_messages = []
        context.tool_feedback_completion_kwargs = None

    @staticmethod
    def _build_assistant_tool_call_message(full_text: str, tool_calls: List[dict]) -> Optional[dict]:
        if not tool_calls:
            return None
        return {
            "role": "assistant",
            "content": full_text or None,
            "tool_calls": [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", ""),
                    },
                }
                for tc in tool_calls
            ],
        }

    @staticmethod
    def _prepare_pending_tool_calls(context: LLMContext, tool_calls: List[dict]) -> List[dict]:
        pending = []
        registry = getattr(context, "tool_registry", None)
        for tc in tool_calls:
            raw_args = tc.get("arguments") or ""
            parsed_args = None
            parse_error = None
            if raw_args:
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    parse_error = str(e)
            else:
                parsed_args = {}

            name = tc.get("name", "")
            known_tool = bool(registry and registry.get(name))
            pending.append({
                "id": tc.get("id", ""),
                "name": name,
                "arguments": raw_args,
                "parsed_args": parsed_args,
                "parse_error": parse_error,
                "known_tool": known_tool,
            })
        return pending

    @staticmethod
    def _json_tool_content(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _normalize_tool_execution_result(
        call: dict,
        *,
        success: bool,
        data,
        error: Optional[str],
        content: Optional[str],
    ) -> dict:
        if not isinstance(data, dict):
            data = {"value": data} if data is not None else {}

        normalized_success = bool(success)
        normalized_error = error
        if content is None or not isinstance(content, str) or not content.strip():
            if normalized_success:
                content = HandlerLLM._json_tool_content(data)
            else:
                content = HandlerLLM._json_tool_content({"error": normalized_error or "Tool execution failed"})
        else:
            try:
                json.loads(content)
            except Exception:
                normalized_success = False
                normalized_error = normalized_error or "Tool result content is not valid JSON"
                content = HandlerLLM._json_tool_content({
                    "error": normalized_error,
                    "raw_content": content,
                })

        return {
            "tool_call_id": call.get("id", ""),
            "name": call.get("name", ""),
            "arguments": call.get("arguments", ""),
            "parsed_args": call.get("parsed_args") if isinstance(call.get("parsed_args"), dict) else None,
            "success": normalized_success,
            "data": data,
            "error": normalized_error,
            "content": content,
        }

    @staticmethod
    def _execute_pending_tool_calls(context: LLMContext) -> List[dict]:
        registry = getattr(context, "tool_registry", None)
        results = []
        for call in getattr(context, "pending_tool_calls", []) or []:
            name = call.get("name", "")
            parsed_args = call.get("parsed_args")
            error = None
            tool_result = None

            if call.get("parse_error"):
                error = f"Invalid tool arguments JSON: {call.get('parse_error')}"
            elif registry is None:
                error = "Tool registry is not initialized"
            elif not call.get("known_tool"):
                error = f"Unknown tool: {name}"
            elif not isinstance(parsed_args, dict):
                error = "Tool arguments must be a JSON object"
            else:
                tool_result = registry.execute(name, parsed_args)

            if tool_result is None:
                results.append(HandlerLLM._normalize_tool_execution_result(
                    call,
                    success=False,
                    data={},
                    error=error or "Tool execution failed",
                    content=None,
                ))
                continue

            success = bool(getattr(tool_result, "success", False))
            data = getattr(tool_result, "data", {}) or {}
            error = getattr(tool_result, "error", None)
            try:
                content = tool_result.to_content_str()
            except Exception as e:
                success = False
                error = f"Tool result serialization failed: {e}"
                content = None

            results.append(HandlerLLM._normalize_tool_execution_result(
                call,
                success=success,
                data=data,
                error=error,
                content=content,
            ))
        return results

    @staticmethod
    def _build_tool_result_messages(tool_execution_results: List[dict]) -> List[dict]:
        messages = []
        for result in tool_execution_results:
            tool_call_id = result.get("tool_call_id", "")
            if not tool_call_id:
                logger.warning(f"Tool result missing tool_call_id: {result}")
            content = result.get("content")
            if content is None or not isinstance(content, str) or not content.strip():
                content = HandlerLLM._json_tool_content({"error": "Missing tool result content"})
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })
        return messages

    @staticmethod
    def _build_tool_feedback_messages(base_messages: List[dict], context: LLMContext) -> List[dict]:
        feedback_messages = [dict(message) for message in base_messages]
        assistant_tool_call_message = getattr(context, "last_tool_call_message", None)
        if assistant_tool_call_message:
            feedback_messages.append(dict(assistant_tool_call_message))
        feedback_messages.extend(
            dict(message)
            for message in (getattr(context, "pending_tool_result_messages", []) or [])
        )
        return feedback_messages

    @staticmethod
    def _build_tool_feedback_completion_kwargs(
        context: LLMContext,
        feedback_messages: List[dict],
    ) -> dict:
        return {
            "model": context.model_name,
            "messages": feedback_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": DEEPSEEK_DISABLE_THINKING_EXTRA_BODY,
        }

    @staticmethod
    def _prepare_tool_feedback_request(
        context: LLMContext,
        base_messages: List[dict],
        stream_key: Optional[str],
        turn_id: Optional[str],
    ) -> bool:
        context.tool_feedback_messages = []
        context.tool_feedback_completion_kwargs = None

        assistant_tool_call_message = getattr(context, "last_tool_call_message", None)
        tool_result_messages = getattr(context, "pending_tool_result_messages", []) or []
        ready = bool(assistant_tool_call_message and tool_result_messages)
        if ready:
            context.tool_feedback_messages = HandlerLLM._build_tool_feedback_messages(
                base_messages,
                context,
            )
            context.tool_feedback_completion_kwargs = HandlerLLM._build_tool_feedback_completion_kwargs(
                context,
                context.tool_feedback_messages,
            )

        audit_event(
            context,
            "llm_tool_feedback_ready",
            stream_key=stream_key,
            turn_id=turn_id,
            llm_stage="tool_feedback",
            model=context.model_name,
            ready=ready,
            has_assistant_tool_call_message=bool(assistant_tool_call_message),
            tool_result_message_count=len(tool_result_messages),
            feedback_message_count=len(context.tool_feedback_messages),
            completion_kwargs_ready=bool(context.tool_feedback_completion_kwargs),
        )
        return ready

    @staticmethod
    def _should_skip_tool_feedback_response(context: LLMContext) -> bool:
        for result in getattr(context, "tool_execution_results", []) or []:
            if not result.get("success"):
                continue
            data = result.get("data") or {}
            if result.get("name") == "music_control" or data.get("type") == "music.control":
                if data.get("action") == "stop":
                    return True
        return False

    @staticmethod
    def _run_tool_feedback_response(
        context: LLMContext,
        output_definition,
        streamer,
        stream_key: Optional[str],
        turn_id: Optional[str],
    ) -> Tuple[str, bool]:
        kwargs = getattr(context, "tool_feedback_completion_kwargs", None)
        if not kwargs:
            logger.warning("Tool feedback response requested without completion kwargs")
            return "", False

        audit_event(
            context,
            "llm_tool_feedback_input",
            stream_key=stream_key,
            turn_id=turn_id,
            llm_stage="tool_feedback",
            model=context.model_name,
            messages=kwargs.get("messages", []),
            has_tools="tools" in kwargs,
            has_tool_choice="tool_choice" in kwargs,
        )
        completion = context.client.chat.completions.create(**kwargs)
        full_text, feedback_tool_calls, cancelled = HandlerLLM._stream_completion_response(
            context,
            completion,
            output_definition,
            streamer,
            stream_key,
        )
        if feedback_tool_calls:
            logger.warning(
                "Tool feedback response returned unexpected tool_calls; "
                f"ignoring {len(feedback_tool_calls)} call(s) in this phase"
            )
        audit_event(
            context,
            "llm_tool_feedback_output",
            stream_key=stream_key,
            turn_id=turn_id,
            llm_stage="tool_feedback",
            model=context.model_name,
            output_text=full_text,
            success=not cancelled,
            cancelled=cancelled,
            unexpected_tool_call_count=len(feedback_tool_calls),
            unexpected_tool_calls=feedback_tool_calls,
        )
        return full_text, cancelled

    @staticmethod
    def _dispatch_music_tool_results(
        context: LLMContext,
        tool_execution_results: List[dict],
        output_definition,
        streamer,
        stream_key: Optional[str],
        original_text: str,
    ) -> bool:
        dispatched = False
        for result in tool_execution_results:
            if not result.get("success"):
                continue
            data = result.get("data") or {}
            tool_name = result.get("name")
            if tool_name == "music_control" or data.get("type") == "music.control":
                action = data.get("action")
                if not action:
                    continue
                if action == "stop":
                    HandlerLLM._set_music_player_active(context, False)
                    HandlerLLM._mark_shared_music_status(context, "stopped", "server_control_stop")
                elif action in {"pause", "resume", "next", "volume", "mute", "unmute"}:
                    HandlerLLM._set_music_player_active(context, True)
                    if action == "pause":
                        HandlerLLM._mark_shared_music_status(context, "paused", "server_control_pause")
                    elif action == "resume":
                        HandlerLLM._mark_shared_music_status(context, "playing", "server_control_resume")

                output = DataBundle(output_definition)
                output.set_main_data("")
                output.add_meta("client_action", {
                    "type": "music.control",
                    "action": action,
                    "delta": data.get("delta"),
                    "hints": data.get("hints") or ["停止", "暂停", "继续", "下一首", "音量小一点"],
                })
                streamer.stream_data(output)
                logger.info(
                    f"Music client_action dispatch: type=music.control stream_key={stream_key} "
                    f"action={action} delta={data.get('delta')} active={context.music_player_active} via=tool_call"
                )
                if action == "stop":
                    context.emit_signal(
                        ChatSignal(
                            type=ChatSignalType.INTERRUPT,
                            source_type=ChatSignalSourceType.HANDLER,
                            source_name=context.owner or "LLMOpenAICompatible",
                            signal_data={
                                "reason": "music_stop",
                                "trigger_text": (original_text or "")[:100],
                            },
                        )
                    )
                    logger.info("Music stop emitted interrupt from tool_call to clear pending avatar response streams")
                if not context.output_texts:
                    context.output_texts = f"music.control:{action}"
                dispatched = True
                continue

            if tool_name == "music_request":
                play_url = data.get("play_url") or ""
                if not play_url:
                    continue
                selected = data.get("selected") or {}
                song_title = selected.get("title") or selected.get("name") or data.get("query") or ""
                artist = selected.get("artist") or ""
                if not artist and isinstance(selected.get("artists"), list):
                    artist = " / ".join(selected.get("artists") or [])
                source = data.get("source") or ""
                candidates = data.get("candidates") or []

                HandlerLLM._set_music_player_active(context, True)
                HandlerLLM._mark_shared_music_status(context, "loading", "server_play_dispatched")
                output = DataBundle(output_definition)
                output.set_main_data("")
                output.add_meta("client_action", {
                    "type": "music.play",
                    "title": song_title,
                    "artist": artist,
                    "url": play_url,
                    "source": source,
                    "query": data.get("query") or "",
                    "candidates": candidates,
                    "hints": ["停止", "暂停", "继续", "下一首", "音量小一点"],
                })
                streamer.stream_data(output)
                logger.info(
                    f"Music client_action dispatch: type=music.play stream_key={stream_key} "
                    f"title={song_title!r} artist={artist!r} source={source or '-'} "
                    f"url={_summarize_url_for_log(play_url)} via=tool_call"
                )
                if not context.output_texts:
                    context.output_texts = data.get("message") or f"正在播放《{song_title}》"
                dispatched = True
        return dispatched

    @staticmethod
    def _handle_tool_calls(
        context: LLMContext,
        full_text: str,
        tool_calls: List[dict],
        output_definition,
        streamer,
        stream_key: Optional[str],
        turn_id: Optional[str],
        original_text: str = "",
        emit_empty_fallback: bool = True,
    ) -> None:
        if not tool_calls:
            return
        assistant_tool_call_message = HandlerLLM._build_assistant_tool_call_message(
            full_text, tool_calls
        )
        pending_tool_calls = HandlerLLM._prepare_pending_tool_calls(context, tool_calls)
        context.last_tool_calls = tool_calls
        context.last_tool_call_message = assistant_tool_call_message
        context.pending_tool_calls = pending_tool_calls

        executed = bool(context.enable_tool_execution)
        tool_side_effect_dispatched = False
        if executed:
            context.tool_execution_results = HandlerLLM._execute_pending_tool_calls(context)
            context.pending_tool_result_messages = HandlerLLM._build_tool_result_messages(
                context.tool_execution_results
            )
            tool_side_effect_dispatched = HandlerLLM._dispatch_music_tool_results(
                context,
                context.tool_execution_results,
                output_definition,
                streamer,
                stream_key,
                original_text,
            )
            logger.info(
                "LLM tool_calls executed: "
                f"{len(context.tool_execution_results)} results for {tool_calls}"
            )
        else:
            context.tool_execution_results = []
            context.pending_tool_result_messages = []
            logger.info(
                "LLM returned tool_calls but tool execution is disabled: "
                f"{tool_calls}"
            )

        audit_event(
            context,
            "llm_tool_calls",
            stream_key=stream_key,
            turn_id=turn_id,
            llm_stage="main_response",
            model=context.model_name,
            tool_calls=tool_calls,
            assistant_tool_call_message=assistant_tool_call_message,
            pending_tool_calls=pending_tool_calls,
            tool_call_count=len(tool_calls),
            executed=executed,
            tool_side_effect_dispatched=tool_side_effect_dispatched,
        )
        if executed:
            audit_event(
                context,
                "llm_tool_execution",
                stream_key=stream_key,
                turn_id=turn_id,
                llm_stage="main_response",
                model=context.model_name,
                tool_execution_results=context.tool_execution_results,
                pending_tool_result_messages=context.pending_tool_result_messages,
                tool_call_count=len(pending_tool_calls),
                result_count=len(context.tool_execution_results),
                tool_result_message_count=len(context.pending_tool_result_messages),
                executed=True,
                tool_side_effect_dispatched=tool_side_effect_dispatched,
            )
        if context.output_texts or not emit_empty_fallback:
            return

        fallback = (
            "我已经调用工具确认了，这一步的结果回传还在接入中。"
            if executed else
            "我需要调用工具确认一下，这一步还在接入中。"
        )
        context.output_texts = fallback
        output = DataBundle(output_definition)
        output.set_main_data(fallback)
        streamer.stream_data(output)


    @staticmethod
    def _system_prompt_for_context(context: LLMContext) -> Dict[str, str]:
        base_prompt = context.system_prompt or {"role": "system", "content": ""}
        content = base_prompt.get("content", "")
        runtime = getattr(context.shared_states, "persona_runtime", None)
        if isinstance(runtime, dict):
            persona_prompt = runtime.get("persona_system_prompt")
            if persona_prompt:
                content = f"{content}\n\n【当前角色】\n{persona_prompt}"
        return {"role": base_prompt.get("role", "system"), "content": content}

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

    def _build_scopemem_context(self, context: LLMContext, query: str) -> Optional[dict]:
        if context.scopemem is None:
            return None
        try:
            memories = context.scopemem.search(query)
        except Exception as e:
            logger.warning(f"ScopeMem memory retrieval failed: {e}")
            audit_event(
                context,
                "memory_read",
                turn_id=context.current_audit_turn_id,
                memory_provider="scopemem",
                query=query,
                success=False,
                error=str(e),
            )
            return None
        if not memories:
            logger.info(f"ScopeMem used memories: count=0 query={query[:120]!r}")
            audit_event(
                context,
                "memory_read",
                turn_id=context.current_audit_turn_id,
                memory_provider="scopemem",
                query=query,
                success=True,
                result_count=0,
                items=[],
            )
            return None
        lines = []
        used_chars = 0
        used_items = []
        max_chars = max(300, int(getattr(context.scopemem, "memory_max_chars", 1600) or 1600))
        for index, item in enumerate(memories, start=1):
            text = str(item.get("memory") or item.get("text") or "").strip()
            if not text:
                continue
            line = f"{index}. {text}"
            if used_chars + len(line) + 1 > max_chars:
                break
            lines.append(line)
            used_items.append((index, item, text))
            used_chars += len(line) + 1
        if not lines:
            logger.info(f"ScopeMem used memories: count=0 query={query[:120]!r}")
            audit_event(
                context,
                "memory_read",
                turn_id=context.current_audit_turn_id,
                memory_provider="scopemem",
                query=query,
                success=True,
                result_count=0,
                items=[],
            )
            return None
        details = []
        for index, item, text in used_items:
            score = item.get("score")
            score_text = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
            details.append(f"#{index}{score_text} text={text[:120]!r}")
        logger.info(
            "ScopeMem used memories: "
            f"count={len(used_items)} query={query[:120]!r} "
            f"items={' | '.join(details)}"
        )
        audit_event(
            context,
            "memory_read",
            turn_id=context.current_audit_turn_id,
            memory_provider="scopemem",
            query=query,
            success=True,
            result_count=len(used_items),
            items=[
                {"index": index, "score": item.get("score"), "text": text}
                for index, item, text in used_items
            ],
        )
        return {
            "role": "user",
            "content": "\n".join([
                "以下是从长期记忆中检索到的可能相关信息，只在和用户当前问题相关时参考。",
                "必须服从最高优先级的小伴人设；不要提到长期记忆、ScopeMem、检索或内部系统。",
                "",
                "长期记忆：",
                *lines,
            ]),
        }


    @staticmethod
    def _build_care_guidance(now: datetime) -> Tuple[str, str]:
        minutes = now.hour * 60 + now.minute
        if 5 * 60 + 30 <= minutes < 9 * 60 + 30:
            return (
                "昨晚睡眠、起床情况或早餐，三者选择一个",
                "午饭或晚饭",
            )
        if 9 * 60 + 30 <= minutes < 11 * 60:
            return (
                "今天准备做什么、喝水或出去走走，三者选择一个",
                "机械询问早餐、午饭或晚饭",
            )
        if 11 * 60 <= minutes < 13 * 60 + 30:
            return ("午饭，最多询问一次", "早餐或晚饭")
        if 13 * 60 + 30 <= minutes < 17 * 60:
            return (
                "午休、下午精神或日常活动，三者选择一个",
                "机械询问早餐、午饭或晚饭",
            )
        if 17 * 60 <= minutes < 19 * 60 + 30:
            return ("晚饭或晚上准备做什么，二者选择一个", "早餐或午饭")
        if 19 * 60 + 30 <= minutes < 21 * 60 + 30:
            return (
                "看电视、听评书、散步或今天过得怎么样，选择一个",
                "主动询问早餐、午饭或晚饭",
            )
        if 21 * 60 + 30 <= minutes < 23 * 60 + 30:
            return (
                "累不累、准备休息没有或今晚睡觉是否方便，选择一个",
                "主动询问早餐、午饭或晚饭",
            )
        return (
            "为什么还没有休息或是不是身体不舒服，选择一个",
            "主动询问早餐、午饭或晚饭",
        )

    @staticmethod
    def _used_address_recently(context: LLMContext, address: str, recent_turns: int = 2) -> bool:
        if not address:
            return False
        try:
            history = getattr(context, "history", None)
            messages = getattr(history, "message_history", None)
            if not messages:
                return False
            avatar_messages = [
                getattr(message, "content", "")
                for message in messages
                if getattr(message, "role", None) == "avatar"
            ]
            for text in avatar_messages[-recent_turns:]:
                if address in str(text or "")[:15]:
                    return True
        except Exception:
            return False
        return False

    def _build_current_time_context(self, context: LLMContext) -> dict:
        now, timezone_label = self._get_local_now(context)
        allowed_care, forbidden_care = self._build_care_guidance(now)
        weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_cn[now.weekday()]
        runtime = getattr(getattr(context, "shared_states", None), "persona_runtime", None)
        address = runtime.get("address_to_elder") if isinstance(runtime, dict) else None
        address = address.strip() if isinstance(address, str) else ""
        if address and self._used_address_recently(context, address):
            address_guidance = f"最近回复已经使用过称呼“{address}”，本轮不要再次使用该称呼。"
        elif address:
            address_guidance = f"本轮可选称呼为“{address}”，没有必要时不要使用；同一回复最多使用一次。"
        else:
            address_guidance = "本轮没有指定称呼，直接自然回答。"
        content = "\n".join([
            "以下是系统本轮运行状态，必须遵守，但不要向用户提及这是内部信息。回答任何涉及今天、明天、昨天、日期、星期、当前时间、天气日期或日程安排的问题时，必须优先使用这一信息。",
            "不要使用联网搜索结果或模型记忆猜测当前日期时间。不要向用户提到这是内部注入。",
            f"本地时区: {timezone_label}",
            f"当前日期时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"当前日期: {now.strftime('%Y年%m月%d日')}",
            f"星期: {weekday}",
            f"当前允许的主动关怀主题: {allowed_care}",
            f"当前禁止的主动关怀主题: {forbidden_care}",
            "用户有明确问题时，优先直接回答，不要额外追加主动关怀。",
            "主动关怀不是每轮必须执行，一次只能问一个问题。",
            address_guidance,
        ])
        return {"role": "system", "content": content}

    def _build_local_time_context(self, context: LLMContext, query: str) -> Optional[dict]:
        if not self._should_inject_local_time(query):
            return None
        return self._build_current_time_context(context)

    @staticmethod
    def _should_inject_local_time(query: str) -> bool:
        normalized = re.sub(r"\s+", "", query or "")
        if not normalized:
            return False
        patterns = (
            r"(现在|当前|此刻).{0,6}(几点|时间|日期)",
            r"(几点|几时|报时|当前时间|现在时间|告诉我时间|看下时间)",
            r"(今天|明天|昨天|后天|前天).{0,6}(几号|日期|星期几|周几|礼拜几)",
            r"(今天|现在).{0,4}(是)?(星期|周|礼拜)",
            r"(今天|现在).{0,4}(几月几号|几号)",
            r"今天是什么日子",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _get_local_now(context: LLMContext):
        timezone_name = getattr(context, "local_time_timezone", None) or "Asia/Shanghai"
        if ZoneInfo is not None and timezone_name:
            try:
                return datetime.now(ZoneInfo(timezone_name)), timezone_name
            except Exception as e:
                logger.warning(f"Invalid OPENAVATAR_LOCAL_TIMEZONE={timezone_name}: {e}")

        now = datetime.now().astimezone()
        timezone_label = now.tzname() or "local"
        return now, timezone_label

    def _build_bocha_search_context(self, context: LLMContext, query: str) -> str:
        if self._should_inject_local_time(query):
            return ""
        if not self._should_search(context, query):
            return ""
        if not context.bocha_api_key:
            logger.warning("Bocha web search is enabled but BOCHA_API_KEY is not set.")
            audit_event(
                context,
                "search_operation",
                turn_id=context.current_audit_turn_id,
                search_provider="bocha",
                query=query,
                success=False,
                error="BOCHA_API_KEY is not set",
            )
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
            self._record_search_call_if_enabled(
                context,
                query=query,
                results=[],
                formatted="",
                success=False,
                error=str(e),
            )
            audit_event(
                context,
                "search_operation",
                turn_id=context.current_audit_turn_id,
                search_provider="bocha",
                query=query,
                success=False,
                error=str(e),
            )
            return ""

        result_items = [
            {"title": result.title, "url": result.url, "snippet": result.snippet}
            for result in results
        ]
        formatted = format_search_results(results)
        self._record_search_call_if_enabled(
            context,
            query=query,
            results=result_items,
            formatted=formatted,
            success=True,
        )
        audit_event(
            context,
            "search_operation",
            turn_id=context.current_audit_turn_id,
            search_provider="bocha",
            query=query,
            success=True,
            result_count=len(result_items),
            results=result_items,
            formatted=formatted,
        )
        if not formatted:
            return ""
        return (
            "以下是实时联网搜索结果。回答用户时优先参考这些结果；"
            "如果搜索结果不足或相互矛盾，请明确说明不确定。"
            "回答中可以简短提及来源。\n\n"
            f"{formatted}"
        )

    def _record_search_call_if_enabled(
        self,
        context: LLMContext,
        *,
        query: str,
        results,
        formatted: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        log_path = os.getenv("OPENAVATAR_SEARCH_CALL_LOG")
        if not log_path:
            return
        try:
            log_dir = os.path.dirname(log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            conversation_time, timezone_label = self._get_local_now(context)
            record = {
                "schema": "openavatarchat.search_call.v1",
                "conversation_time": conversation_time.isoformat(),
                "conversation_timezone": timezone_label,
                "recorded_at": datetime.now().astimezone().isoformat(),
                "session_id": getattr(context, "session_id", None),
                "turn_id": getattr(context, "current_audit_turn_id", None),
                "query": query,
                "success": success,
                "results": results,
                "formatted_results": formatted,
            }
            if error:
                record["error"] = error
            with open(log_path, "a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.warning(f"Search call log write failed: {e}")

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
    def _get_shared_music_status(context) -> dict:
        shared_states = getattr(context, "shared_states", None)
        status = getattr(shared_states, "music_status", None) if shared_states is not None else None
        return status if isinstance(status, dict) else {}

    @staticmethod
    def _get_shared_music_state(context) -> str:
        state = HandlerLLM._get_shared_music_status(context).get("state")
        return str(state or "").strip().lower()

    @staticmethod
    def _is_music_player_active(context) -> bool:
        shared_states = getattr(context, "shared_states", None)
        status = getattr(shared_states, "music_status", None) if shared_states is not None else None
        if isinstance(status, dict) and status.get("state"):
            return str(status.get("state") or "").strip().lower() in MUSIC_ACTIVE_STATES
        if shared_states is not None and hasattr(shared_states, "music_player_active"):
            return bool(getattr(shared_states, "music_player_active", False))
        return bool(getattr(context, "music_player_active", False))

    @staticmethod
    def _set_music_player_active(context, active: bool) -> None:
        context.music_player_active = active
        shared_states = getattr(context, "shared_states", None)
        if shared_states is not None:
            shared_states.music_player_active = active

    @staticmethod
    def _mark_shared_music_status(context, state: str, reason: str) -> None:
        shared_states = getattr(context, "shared_states", None)
        if shared_states is None:
            return
        status = dict(getattr(shared_states, "music_status", None) or {})
        status.update({
            "state": state,
            "reason": reason,
            "received_at": time.time(),
            "source": "server",
        })
        shared_states.music_status = status

    @staticmethod
    def _should_handle_music_control_direct(context, text: str, control: dict) -> bool:
        action = (control or {}).get("action")
        if not action:
            return False
        state = HandlerLLM._get_shared_music_state(context)
        active = HandlerLLM._is_music_player_active(context)
        if action == "stop":
            return active or HandlerLLM._is_explicit_music_stop_text(text)
        if action == "pause":
            return active or state in MUSIC_DIRECT_PAUSE_STATES
        if action in {"resume", "next", "volume", "mute", "unmute"}:
            return active
        return False

    @staticmethod
    def _is_explicit_music_stop_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return any(
            keyword in compact for keyword in (
                "停止音乐",
                "停止播放",
                "停止放歌",
                "停止这首歌",
                "结束播放",
                "结束音乐",
                "关闭音乐",
                "关掉音乐",
                "关掉播放",
                "退出音乐",
                "别放了",
                "别播了",
                "别播放了",
                "不要放了",
                "不要播放了",
                "不听了",
            )
        )

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
        compact = re.sub(r"[\s，。！？!?,;；：:\"'“”‘’（）()【】\[\]《》、.]+", "", normalized)
        if any(
            keyword in compact for keyword in (
    "莫播了",
    "莫放歌了",
    "莫放音乐了",
    "不播了",
    "不听歌了",
    "歌不听了",
    "这歌不听了",
    "这首歌不听了",
    "先停一哈",
    "给我停了",
    "给我关了",
    "帮我关了",
    "把歌关了",
    "把音乐关了",
    "算了不听了",
    "停止放个",
    "停止这手歌",
    "关掉放个",
    "停止播放",
    "停止放歌",
    "停止这首歌", 
    "结束播放", 
    "结束音乐", 
    "关闭音乐", 
    "关掉音乐", 
    "关掉播放", 
    "退出音乐", 
    "别放了", 
    "别播了", 
    "别播放了", 
    "不要放了", 
    "不要播放了"
            )
        ):
            return {"action": "stop"}
        if compact in {"暂停", "停"} or any(
            keyword in compact for keyword in (
    "暂停音乐",
    "暂停播放",
    "先暂停",
    "先暂停一下",
    "先暂停一哈",
    "暂停一下",
    "暂停一哈",
    "暂停哈",
    "停一下",
    "停一哈",
    "停哈",
    "先停一下",
    "先停哈",
    "给我暂停一下",
    "帮我暂停一下",
    "给我停一下",
    "帮我停一下",
    "暂定音乐",
    "暂听音乐",
    "暂定播放",
    "暂听播放",
    "暂定一下",
    "暂听一下",
    "站停一下")
        ):
            return {"action": "pause"}
        if compact in {"继续", "恢复"} or any(
            keyword in compact for keyword in (
    "继续放",
    "继续放歌",
    "继续放音乐",
    "继续播",
    "继续听",
    "继续听歌",
    "继续听音乐",
    "恢复音乐",
    "恢复放歌",
    "恢复放音乐",
    "恢复一下",
    "恢复一哈",
    "接着播",
    "接着放歌",
    "接着放音乐",
    "接着听",
    "接着听歌",
    "接着听音乐",
    "接到放",
    "接倒放",
    "接到播",
    "接倒播",
    "再放",
    "再播",
    "再放一哈",
    "再播一哈")
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
            HandlerLLM._set_music_player_active(context, True)
            HandlerLLM._mark_shared_music_status(context, "loading", "server_play_dispatched")
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
            HandlerLLM._set_music_player_active(context, False)
            HandlerLLM._mark_shared_music_status(context, "stopped", "server_control_stop")
        elif action in {"pause", "resume", "next", "volume", "mute", "unmute"}:
            HandlerLLM._set_music_player_active(context, True)
            if action == "pause":
                HandlerLLM._mark_shared_music_status(context, "paused", "server_control_pause")
            elif action == "resume":
                HandlerLLM._mark_shared_music_status(context, "playing", "server_control_resume")
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
