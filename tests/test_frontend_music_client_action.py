from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_STORE = ROOT / "src/service/frontend_service/frontend/src/renderer/src/store/chat.ts"


def test_chat_store_consumes_music_client_actions():
    source = CHAT_STORE.read_text()

    assert "client_action" in source
    assert "handleClientAction" in source
    assert "music.play" in source
    assert "music.control" in source
    assert "new Audio" in source
    assert "playMusicAction" in source
    assert "controlMusicAction" in source
