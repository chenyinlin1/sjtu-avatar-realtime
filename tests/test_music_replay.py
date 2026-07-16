import time
from types import SimpleNamespace

import pytest

from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundleDefinition,
    DataBundleEntry,
)
from handlers.agent.tools.music_control import MusicControlTool
from handlers.llm.openai_compatible.llm_handler_openai_compatible import HandlerLLM


@pytest.mark.parametrize(
    "text",
    [
        "停下来吧，我不想听歌。",
        "我不想再听歌了。",
        "不想听这首歌了。",
    ],
)
def test_natural_stop_phrases_are_normalized_to_stop(text):
    assert HandlerLLM._extract_music_control(text) == {"action": "stop"}


def test_stop_intent_is_forced_through_music_control_tool():
    context = SimpleNamespace(
        enable_tool_execution=True,
        tool_choice="auto",
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "music_control"},
            }
        ],
        music_player_active=True,
        shared_states=SimpleNamespace(
            music_player_active=True,
            music_status={"state": "playing"},
        ),
    )
    text = "停下来吧，我不想听歌。"
    control = HandlerLLM._extract_music_control(text)

    assert HandlerLLM._should_route_music_stop_through_tool(
        context,
        text,
        control,
    ) is True
    assert HandlerLLM._tool_choice_for_turn(context, text) == {
        "type": "function",
        "function": {"name": "music_control"},
    }


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


def test_replay_uses_music_play_for_speaker_compatibility():
    context = SimpleNamespace(
        shared_states=SimpleNamespace(
            music_status={
                "state": "ended",
                "title": "爱情转移",
                "artist": "陈奕迅",
                "url": "https://example.com/love-transfer.mp3",
            },
            music_player_active=False,
            last_music_track=None,
        ),
    )

    action = HandlerLLM._build_music_control_client_action(context, "replay")

    assert action["type"] == "music.play"
    assert action["title"] == "爱情转移"
    assert action["artist"] == "陈奕迅"
    assert action["url"] == "https://example.com/love-transfer.mp3"


def test_replay_after_stop_uses_cached_track_and_is_directly_dispatched():
    context = SimpleNamespace(
        music_player_active=False,
        shared_states=SimpleNamespace(
            music_status={"state": "stopped"},
            music_player_active=False,
            last_music_track={
                "title": "爱情转移",
                "artist": "陈奕迅",
                "url": "https://example.com/love-transfer.mp3",
                "source": "music-api",
                "query": "爱情转移 陈奕迅",
                "candidates": [],
            },
        ),
    )

    assert HandlerLLM._should_handle_music_control_direct(
        context,
        "重新播放",
        {"action": "replay"},
    ) is True


def test_direct_replay_emits_music_play_instead_of_replay_control():
    history_messages = []
    context = SimpleNamespace(
        active_stream_keys=set(),
        music_player_active=False,
        shared_states=SimpleNamespace(
            music_status={"state": "stopped"},
            music_player_active=False,
            last_music_track={
                "title": "爱情转移",
                "artist": "陈奕迅",
                "url": "https://example.com/love-transfer.mp3",
                "source": "music-api",
                "query": "爱情转移 陈奕迅",
                "candidates": [],
            },
        ),
        history=SimpleNamespace(add_message=history_messages.append),
        input_texts="重新播放",
        output_texts="",
    )
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))

    class RecordingStreamer:
        def __init__(self):
            self.calls = []

        def stream_data(self, bundle, **kwargs):
            self.calls.append((bundle, kwargs))

    streamer = RecordingStreamer()

    HandlerLLM._handle_music_control(
        None,
        context,
        {"action": "replay"},
        definition,
        streamer,
        "stream-test",
        "重新播放",
    )

    action = streamer.calls[0][0].metadata["client_action"]
    assert action["type"] == "music.play"
    assert action["url"] == "https://example.com/love-transfer.mp3"
    assert context.shared_states.music_status["state"] == "loading"


def test_tool_replay_emits_music_play_for_speaker_compatibility():
    context = SimpleNamespace(
        music_player_active=False,
        output_texts="",
        shared_states=SimpleNamespace(
            music_status={
                "state": "ended",
                "title": "爱情转移",
                "artist": "陈奕迅",
                "url": "https://example.com/love-transfer.mp3",
            },
            music_player_active=False,
            last_music_track=None,
        ),
    )
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))

    class RecordingStreamer:
        def __init__(self):
            self.bundles = []

        def stream_data(self, bundle, **_kwargs):
            self.bundles.append(bundle)

    streamer = RecordingStreamer()
    dispatched = HandlerLLM._dispatch_music_tool_results(
        context,
        [
            {
                "name": "music_control",
                "success": True,
                "data": {"type": "music.control", "action": "replay"},
            }
        ],
        definition,
        streamer,
        "stream-test",
        "重新播放",
    )

    assert dispatched is True
    action = streamer.bundles[0].metadata["client_action"]
    assert action["type"] == "music.play"
    assert action["url"] == "https://example.com/love-transfer.mp3"


def _avatar_text_definition():
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))
    return definition


def _stop_context(*, ack_timeout=0.01):
    return SimpleNamespace(
        music_player_active=True,
        output_texts="",
        music_stop_ack_timeout_seconds=ack_timeout,
        shared_states=SimpleNamespace(
            music_status={
                "state": "playing",
                "received_at": time.time() - 1,
            },
            music_player_active=True,
            last_music_track=None,
        ),
    )


def _stop_tool_result():
    return {
        "name": "music_control",
        "success": True,
        "data": {
            "type": "music.control",
            "action": "stop",
        },
        "error": None,
        "content": "{}",
    }


def test_stop_confirmation_is_emitted_only_after_client_stopped_ack():
    context = _stop_context()
    result = _stop_tool_result()

    class AckingStreamer:
        def __init__(self):
            self.bundles = []

        def stream_data(self, bundle, **_kwargs):
            self.bundles.append(bundle)
            action = bundle.metadata.get("client_action")
            if action and action.get("action") == "stop":
                context.shared_states.music_status = {
                    "state": "stopped",
                    "reason": "stop_control",
                    "received_at": time.time(),
                }
                context.shared_states.music_player_active = False

    streamer = AckingStreamer()
    dispatched = HandlerLLM._dispatch_music_tool_results(
        context,
        [result],
        _avatar_text_definition(),
        streamer,
        "stream-stop",
        "停下来吧，我不想听歌。",
    )

    assert dispatched is True
    assert streamer.bundles[0].metadata["client_action"]["action"] == "stop"
    assert streamer.bundles[-1].get_main_data() == "已经停了。"
    assert context.output_texts == "已经停了。"
    assert result["success"] is True
    assert result["data"]["client_confirmed"] is True


def test_stop_timeout_never_claims_music_has_stopped():
    context = _stop_context(ack_timeout=0)
    result = _stop_tool_result()

    class RecordingStreamer:
        def __init__(self):
            self.bundles = []

        def stream_data(self, bundle, **_kwargs):
            self.bundles.append(bundle)

    streamer = RecordingStreamer()
    dispatched = HandlerLLM._dispatch_music_tool_results(
        context,
        [result],
        _avatar_text_definition(),
        streamer,
        "stream-stop-timeout",
        "停下来吧，我不想听歌。",
    )

    assert dispatched is True
    assert context.output_texts == "停止指令发出去了，但还没确认停下来。"
    assert "已经停" not in context.output_texts
    assert result["success"] is False
    assert result["data"]["client_confirmed"] is False
