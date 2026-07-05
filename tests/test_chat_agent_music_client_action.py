from handlers.agent.chat_agent_handler import ChatAgentHandler
from handlers.agent.tools.base_tool import ToolResult
from handlers.agent.tools.music_control import MusicControlTool


AGENT_TOOLS_CONFIG = (
    "config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006_agent_tools.yaml"
)


def test_music_request_result_builds_music_play_client_action():
    result = ToolResult(
        success=True,
        data={
            "query": "青花瓷 周杰伦",
            "source": "https://music.example.test",
            "selected": {
                "title": "青花瓷",
                "artist": "周杰伦",
            },
            "play_url": "https://music.example.test/song.mp3",
            "candidates": [
                {"title": "青花瓷", "artist": "周杰伦"},
            ],
        },
    )

    action = ChatAgentHandler._build_client_action_from_tool_result("music_request", result)

    assert action == {
        "type": "music.play",
        "title": "青花瓷",
        "artist": "周杰伦",
        "url": "https://music.example.test/song.mp3",
        "source": "https://music.example.test",
        "query": "青花瓷 周杰伦",
        "candidates": [
            {"title": "青花瓷", "artist": "周杰伦"},
        ],
        "hints": ["暂停", "继续", "下一首", "音量小一点"],
    }


def test_music_control_result_builds_music_control_client_action():
    result = ToolResult(
        success=True,
        data={
            "type": "music.control",
            "action": "pause",
            "hints": ["暂停", "继续"],
        },
    )

    action = ChatAgentHandler._build_client_action_from_tool_result("music_control", result)

    assert action == {
        "type": "music.control",
        "action": "pause",
        "hints": ["暂停", "继续"],
    }


def test_music_control_tool_supports_stop_action():
    tool = MusicControlTool()

    assert "stop" in tool.parameters["properties"]["action"]["enum"]

    result = tool.execute({"action": "stop"})
    action = ChatAgentHandler._build_client_action_from_tool_result(
        "music_control",
        result,
    )

    assert result.success is True
    assert action == {
        "type": "music.control",
        "action": "stop",
        "hints": ["暂停", "继续", "下一首", "音量小一点"],
    }


def test_agent_tools_config_maps_exit_playback_to_music_control():
    with open(AGENT_TOOLS_CONFIG, "r", encoding="utf-8") as f:
        source = f.read()

    assert "退出播放" in source
    assert "停止播放" in source
    assert "music_control" in source


def test_direct_music_tool_call_extracts_generic_artist_request():
    call = ChatAgentHandler._build_direct_music_tool_call("放一首周杰伦的歌")

    assert call == {
        "name": "music_request",
        "args": {
            "song_name": "周杰伦",
            "limit": 5,
        },
    }


def test_direct_music_tool_call_extracts_named_song_request():
    call = ChatAgentHandler._build_direct_music_tool_call("给我放一个黄梅戏")

    assert call == {
        "name": "music_request",
        "args": {
            "song_name": "黄梅戏",
            "limit": 5,
        },
    }


def test_direct_music_tool_call_extracts_stop_request():
    call = ChatAgentHandler._build_direct_music_tool_call("退出播放")

    assert call == {
        "name": "music_control",
        "args": {
            "action": "stop",
        },
    }
