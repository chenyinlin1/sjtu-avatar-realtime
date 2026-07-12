from types import SimpleNamespace

import pytest

from handlers.agent.tools.music_control import MusicControlTool
from handlers.llm.openai_compatible.llm_handler_openai_compatible import HandlerLLM


@pytest.mark.parametrize(
    "text",
    [
        "重播一下啦！",
        "再重放一下吧。",
        "重新播放",
        "从头放",
        "再听一遍",
        "再放一遍",
    ],
)
def test_replay_phrases_are_normalized_to_replay(text):
    assert HandlerLLM._extract_music_control(text) == {"action": "replay"}


def test_restart_tool_alias_is_normalized_to_replay():
    result = MusicControlTool().execute({"action": "restart"})

    assert result.success is True
    assert result.data["type"] == "music.control"
    assert result.data["action"] == "replay"


def test_replay_is_directly_dispatched_after_natural_end():
    context = SimpleNamespace(
        music_player_active=False,
        shared_states=SimpleNamespace(
            music_player_active=False,
            music_status={"state": "ended", "title": "我爱北京天安门"},
        ),
    )

    assert HandlerLLM._should_handle_music_control_direct(
        context,
        "重播一下",
        {"action": "replay"},
    ) is True


def test_resume_remains_distinct_from_replay():
    assert HandlerLLM._extract_music_control("继续播放") == {"action": "resume"}
    assert HandlerLLM._extract_music_control("再播一哈") == {"action": "resume"}
