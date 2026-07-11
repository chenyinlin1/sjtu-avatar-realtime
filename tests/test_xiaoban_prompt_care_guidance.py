from datetime import datetime
from types import SimpleNamespace

import pytest

from handlers.llm.openai_compatible.chat_history_manager import HistoryMessage
from handlers.llm.openai_compatible.llm_handler_openai_compatible import HandlerLLM
from service.v1_adapter.personas.models import PersonaRecord
from service.v1_adapter.personas.runtime import format_persona_prompt


@pytest.mark.parametrize(
    ("hour", "minute", "allowed_terms", "forbidden_terms"),
    [
        (8, 0, ("睡眠", "起床", "早餐"), ("午饭", "晚饭")),
        (10, 0, ("准备做什么", "喝水", "出去走走"), ("早餐", "午饭", "晚饭")),
        (12, 0, ("午饭",), ("早餐", "晚饭")),
        (15, 0, ("午休", "下午精神", "日常活动"), ("早餐", "午饭", "晚饭")),
        (18, 30, ("晚饭", "晚上准备做什么"), ("早餐", "午饭")),
        (20, 0, ("看电视", "听评书", "散步", "今天过得怎么样"), ("早餐", "午饭", "晚饭")),
        (22, 0, ("累不累", "准备休息", "睡觉"), ("早餐", "午饭", "晚饭")),
        (1, 0, ("为什么还没有休息", "身体不舒服"), ("早餐", "午饭", "晚饭")),
    ],
)
def test_care_guidance_for_representative_times(hour, minute, allowed_terms, forbidden_terms):
    allowed, forbidden = HandlerLLM._build_care_guidance(datetime(2026, 7, 11, hour, minute))

    assert all(term in allowed for term in allowed_terms)
    assert all(term in forbidden for term in forbidden_terms)


@pytest.mark.parametrize(
    ("hour", "minute", "expected_term"),
    [
        (5, 30, "早餐"),
        (9, 30, "喝水"),
        (11, 0, "午饭"),
        (13, 30, "午休"),
        (17, 0, "晚饭"),
        (19, 30, "听评书"),
        (21, 30, "准备休息"),
        (23, 30, "身体不舒服"),
    ],
)
def test_care_guidance_time_boundaries(hour, minute, expected_term):
    allowed, _ = HandlerLLM._build_care_guidance(datetime(2026, 7, 11, hour, minute))

    assert expected_term in allowed


def _context_with_messages(messages):
    return SimpleNamespace(history=SimpleNamespace(message_history=messages))


def test_used_address_recently_finds_address_in_recent_avatar_prefix():
    context = _context_with_messages([
        HistoryMessage(role="avatar", content="今天天气不错。"),
        HistoryMessage(role="avatar", content="爷爷，您慢慢说。"),
    ])

    assert HandlerLLM._used_address_recently(context, "爷爷") is True


def test_used_address_recently_ignores_older_avatar_messages():
    context = _context_with_messages([
        HistoryMessage(role="avatar", content="爷爷，早上好。"),
        HistoryMessage(role="avatar", content="今天天气不错。"),
        HistoryMessage(role="avatar", content="您慢慢说。"),
    ])

    assert HandlerLLM._used_address_recently(context, "爷爷") is False


def test_used_address_recently_ignores_human_messages_and_late_content():
    context = _context_with_messages([
        HistoryMessage(role="human", content="爷爷刚才出门了。"),
        HistoryMessage(role="avatar", content="这句话开头已经超过十五个字符以后才出现爷爷。"),
    ])

    assert HandlerLLM._used_address_recently(context, "爷爷") is False


@pytest.mark.parametrize(
    ("context", "address"),
    [
        (_context_with_messages([HistoryMessage(role="avatar", content="您好。")]), "爷爷"),
        (_context_with_messages([HistoryMessage(role="avatar", content="")]), "爷爷"),
        (_context_with_messages([]), "爷爷"),
        (SimpleNamespace(history=None), "爷爷"),
        (_context_with_messages([HistoryMessage(role="avatar", content="爷爷，您好。")]), ""),
    ],
)
def test_used_address_recently_returns_false_without_recent_match(context, address):
    assert HandlerLLM._used_address_recently(context, address) is False


def test_format_persona_prompt_makes_relationship_and_address_optional():
    record = PersonaRecord(
        persona_id="persona-1",
        elder_id="elder-1",
        tenant_id="tenant-1",
        display_name="小雅",
        relationship="孙女",
        address_to_elder="爷爷",
        self_reference="我",
        gender="女",
        persona_prompt="说话轻快自然",
        created_at=1,
        updated_at=1,
    )

    prompt = format_persona_prompt(record)

    assert "角色展示名：小雅" in prompt
    assert "与老人的关系设定：孙女" in prompt
    assert "关系主要用于调整陪伴语气" in prompt
    assert "可选称呼：爷爷" in prompt
    assert "不要把称呼作为固定开场" in prompt
    assert "同一回复最多使用一次" in prompt
    assert "没有必要时直接省略" in prompt
    assert "角色自称：我" in prompt
    assert "角色性别：女" in prompt
    assert "角色补充设定：说话轻快自然" in prompt
    assert "不要提及 persona_id、内部配置、音色克隆或形象克隆" in prompt


def test_current_time_context_is_system_message_and_blocks_reused_address(monkeypatch):
    context = SimpleNamespace(
        local_time_timezone="Asia/Shanghai",
        shared_states=SimpleNamespace(persona_runtime={"address_to_elder": " 爷爷 "}),
        history=SimpleNamespace(
            message_history=[HistoryMessage(role="avatar", content="爷爷，晚上好。")]
        ),
    )
    monkeypatch.setattr(
        HandlerLLM,
        "_get_local_now",
        staticmethod(lambda _context: (datetime(2026, 7, 11, 22, 0), "Asia/Shanghai")),
    )

    message = HandlerLLM()._build_current_time_context(context)

    assert message["role"] == "system"
    assert "当前允许的主动关怀主题: 累不累" in message["content"]
    assert "当前禁止的主动关怀主题: 主动询问早餐、午饭或晚饭" in message["content"]
    assert "用户有明确问题时，优先直接回答" in message["content"]
    assert "主动关怀不是每轮必须执行，一次只能问一个问题" in message["content"]
    assert "最近回复已经使用过称呼“爷爷”，本轮不要再次使用该称呼" in message["content"]
