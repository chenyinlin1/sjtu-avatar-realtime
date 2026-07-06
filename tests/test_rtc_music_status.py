import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "handlers"))

from chat_engine.contexts.session_context import SharedStates
from service.rtc_service.rtc_stream import RtcStream


def test_rtc_music_status_updates_shared_state_for_active_state():
    shared_states = SharedStates()
    delegate = SimpleNamespace(shared_states=shared_states)
    stream = RtcStream(session_id="test-session")
    stream.client_session_delegate = delegate

    stream._handle_music_status(
        {
            "state": "playing",
            "reason": "play_started",
            "title": "Dao Xiang",
            "url": "https://example.com/song.mp3",
        },
        "req_music_status_001",
    )

    assert shared_states.music_status["state"] == "playing"
    assert shared_states.music_status["request_id"] == "req_music_status_001"
    assert shared_states.music_status["title"] == "Dao Xiang"
    assert shared_states.music_player_active is True
    assert delegate.music_player_active is True


def test_rtc_music_status_clears_active_for_terminal_states():
    for state in ["ended", "stopped", "error"]:
        shared_states = SharedStates(music_player_active=True)
        delegate = SimpleNamespace(shared_states=shared_states)
        stream = RtcStream(session_id="test-session")
        stream.client_session_delegate = delegate

        stream._handle_music_status({"state": state}, f"req_{state}")

        assert shared_states.music_status["state"] == state
        assert shared_states.music_player_active is False
        assert delegate.music_player_active is False


def test_rtc_music_status_ignores_invalid_state():
    shared_states = SharedStates()
    delegate = SimpleNamespace(shared_states=shared_states)
    stream = RtcStream(session_id="test-session")
    stream.client_session_delegate = delegate

    stream._handle_music_status({"state": "not-a-state"}, "req_bad")

    assert shared_states.music_status is None
    assert shared_states.music_player_active is False
