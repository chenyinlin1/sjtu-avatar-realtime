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
