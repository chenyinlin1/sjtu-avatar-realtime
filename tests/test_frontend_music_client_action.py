from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_STORE = ROOT / "src/service/frontend_service/frontend/src/renderer/src/store/chat.ts"
WEBRTC_STORE = ROOT / "src/service/frontend_service/frontend/src/renderer/src/store/webrtc.ts"


def test_chat_store_consumes_music_client_actions():
    source = CHAT_STORE.read_text()

    assert "client_action" in source
    assert "handleClientAction" in source
    assert "music.play" in source
    assert "music.control" in source
    assert "new Audio" in source
    assert "playMusicAction" in source
    assert "controlMusicAction" in source
    assert "[music] client action received" in source
    assert "[music] play requested" in source
    assert "[music] play failed" in source
    assert "[music] audio error" in source
    assert "reportMusicStatus" in source
    assert "play_started" in source
    assert "pause_control" in source
    assert "stop_control" in source


def test_webrtc_data_channel_consumes_music_client_actions():
    source = WEBRTC_STORE.read_text()

    assert "handleClientAction" in source
    assert "consumedClientAction" in source
    assert "consumedClientAction && !payload.text" in source
    assert "[music] WebRTC data channel consumed client_action" in source
    assert "sendMusicStatus" in source
    assert "WsProtocol.MusicStatus" in source
