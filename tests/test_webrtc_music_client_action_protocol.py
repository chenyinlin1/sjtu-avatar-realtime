import json

from handlers.client.ws_client.ws_message_protocol import (
    EchoAvatarText,
    MusicStatus,
    MusicStatusPayload,
    EchoTextPayload,
    MessageHeader,
    MessageType,
    parse_message,
    serialize_message,
)


def test_echo_avatar_text_carries_music_play_client_action_for_speaker_webrtc():
    message = EchoAvatarText(
        header=MessageHeader(name=MessageType.ECHO_AVATAR_TEXT, request_id="req_music_001"),
        payload=EchoTextPayload(
            stream_key="stream_6493816_5",
            mode="increment",
            text="",
            end_of_speech=False,
            metadata={
                "client_action": {
                    "type": "music.play",
                    "title": "Dao Xiang",
                    "artist": "Jay Chou",
                    "url": "https://example.com/song.mp3",
                    "source": "http://music-service.example.com",
                    "query": "Dao Xiang",
                    "candidates": [],
                }
            },
        ),
    )

    wire_message = json.loads(json.dumps(serialize_message(message)))

    assert wire_message["header"]["name"] == "EchoAvatarText"
    assert wire_message["payload"]["text"] == ""
    action = wire_message["payload"]["metadata"]["client_action"]
    assert action["type"] == "music.play"
    assert action["url"] == "https://example.com/song.mp3"

    parsed = parse_message(wire_message)

    assert isinstance(parsed, EchoAvatarText)
    parsed_action = parsed.payload.metadata["client_action"]
    assert parsed_action["type"] == "music.play"
    assert parsed_action["url"] == "https://example.com/song.mp3"


def test_music_status_round_trips_for_speaker_webrtc():
    message = MusicStatus(
        header=MessageHeader(name=MessageType.MUSIC_STATUS, request_id="req_music_status_001"),
        payload=MusicStatusPayload(
            state="playing",
            reason="play_started",
            title="Dao Xiang",
            artist="Jay Chou",
            url="https://example.com/song.mp3",
            position_ms=12000,
            duration_ms=240000,
        ),
    )

    wire_message = json.loads(json.dumps(serialize_message(message)))

    assert wire_message["header"]["name"] == "MusicStatus"
    assert wire_message["payload"]["state"] == "playing"
    assert wire_message["payload"]["position_ms"] == 12000

    parsed = parse_message(wire_message)

    assert isinstance(parsed, MusicStatus)
    assert parsed.payload.state == "playing"
    assert parsed.payload.title == "Dao Xiang"


def test_music_status_rejects_invalid_state():
    wire_message = {
        "header": {"name": "MusicStatus", "request_id": "req_music_status_bad"},
        "payload": {"state": "buffering_forever"},
    }

    assert parse_message(wire_message) is None
