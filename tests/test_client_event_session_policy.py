import json

import pytest
import time
from concurrent.futures import Future
from types import SimpleNamespace
from threading import Event

from chat_engine.data_models.chat_signal_type import ChatSignalType
from handlers.client.ws_client.ws_message_protocol import ClientEvent, parse_message
from service.rtc_service.rtc_stream import RtcStream
from service.rtc_service.session_event_policy import SessionEventPolicyConfig


class DummyDelegate:
    def __init__(self):
        self.shared_states = SimpleNamespace(music_player_active=False)
        self.device_info = {"device_sn": "speaker-1"}
        self.signals = []

    def emit_signal(self, signal):
        self.signals.append(signal)


def make_stream(**policy):
    stream = RtcStream(
        session_id="test-session",
        stream_start_delay=0,
        session_policy_config=policy,
    )
    stream.client_session_delegate = DummyDelegate()
    stream.chat_channel_loop = None
    sent = []
    stream._send_data_channel_json = lambda name, request_id, payload: sent.append(
        {"name": name, "request_id": request_id, "payload": payload}
    )
    return stream, sent


def action_from(message):
    return message["payload"]["metadata"]["client_action"]


def event_payload(event_type, data=None, ts=None):
    return {
        "type": event_type,
        "ts": ts if ts is not None else int(time.time() * 1000),
        "data": data or {},
    }


def test_invalid_silence_threshold_order_is_rejected():
    with pytest.raises(ValueError, match="silence_level2_ms"):
        SessionEventPolicyConfig(
            silence_level1_ms=90000,
            silence_level2_ms=60000,
        )


def test_client_event_protocol_parses_and_rejects_invalid_payload():
    raw = {
        "header": {"name": "ClientEvent", "request_id": "evt-1"},
        "payload": event_payload("wake"),
    }
    parsed = parse_message(raw)
    assert isinstance(parsed, ClientEvent)
    assert parsed.payload.type == "wake"

    raw["payload"].pop("ts")
    assert parse_message(raw) is None


def test_silence_is_thresholded_deduplicated_and_reset_by_user_activity():
    stream, sent = make_stream(silence_level1_ms=60000, silence_level2_ms=90000)

    stream._handle_client_event(
        event_payload("user_silence", {"level": 1, "silence_ms": 59999}), "evt-low"
    )
    assert sent == []

    level1 = event_payload("user_silence", {"level": 1, "silence_ms": 60000})
    stream._handle_client_event(level1, "evt-l1")
    assert action_from(sent[-1])["then"] == "keep"

    stream._handle_client_event(level1, "evt-l1")
    assert len(sent) == 1

    stream.note_user_activity("human_text")
    stream._handle_client_event(level1, "evt-l1-second-episode")
    assert len(sent) == 2

    stream._handle_client_event(
        event_payload("user_silence", {"level": 2, "silence_ms": 90000}), "evt-l2"
    )
    assert action_from(sent[-1])["then"] == "end"
    assert action_from(sent[-1])["reason"] == "idle_timeout"
    assert stream._session_ending is True


def test_silence_is_ignored_while_music_or_avatar_output_is_active():
    stream, sent = make_stream()
    stream.client_session_delegate.shared_states.music_player_active = True
    stream._handle_client_event(
        event_payload("user_silence", {"level": 1, "silence_ms": 60000}), "evt-music"
    )
    assert sent == []

    stream.client_session_delegate.shared_states.music_player_active = False
    stream._av_sync_current_speech_id = "speech-1"
    stream._handle_client_event(
        event_payload("user_silence", {"level": 1, "silence_ms": 60000}), "evt-speaking"
    )
    assert sent == []


def test_ui_end_interrupts_and_sends_session_end_once():
    stream, sent = make_stream()
    payload = event_payload("ui_end")
    stream._handle_client_event(payload, "evt-ui")
    stream._handle_client_event(payload, "evt-ui-duplicate-source")

    assert len(sent) == 1
    assert action_from(sent[0])["type"] == "session.end"
    assert action_from(sent[0])["reason"] == "ui_end"
    assert len(stream.client_session_delegate.signals) == 1
    assert stream.client_session_delegate.signals[0].type == ChatSignalType.INTERRUPT


def test_reminder_capture_emits_create_and_ack_confirms_after_success():
    stream, sent = make_stream()
    extracted = Future()
    extracted.set_result({
        "kind": "custom",
        "title": "吃药",
        "remind_at": int(time.time() * 1000) + 60000,
        "repeat": "none",
        "speak_text": "该吃药啦",
        "timezone": "Asia/Shanghai",
    })
    stream.session_event_policy._reminder_capture_inflight = True
    stream.session_event_policy._finish_reminder_capture(extracted, "evt-capture")

    create_action = action_from(sent[0])
    assert create_action["type"] == "reminder.create"
    assert create_action["title"] == "吃药"
    assert stream.session_event_policy._reminder_capture_inflight is False

    stream._handle_client_event(
        event_payload("reminder_ack", {"action_id": create_action["action_id"], "ok": True}),
        "evt-rem-ack",
    )
    confirmation = action_from(sent[1])
    assert confirmation["type"] == "say"
    assert confirmation["reason"] == "reminder_create_ack"


def test_failed_reminder_extraction_asks_user_to_repeat_instead_of_claiming_success():
    stream, sent = make_stream()
    failed = Future()
    failed.set_result(None)
    stream.session_event_policy._finish_reminder_capture(failed, "evt-capture-failed")
    action = action_from(sent[0])
    assert action["type"] == "say"
    assert action["reason"] == "reminder_capture_failed"


def test_reminder_due_uses_deterministic_delivery_policy():
    normal, normal_sent = make_stream()
    normal._handle_client_event(
        event_payload(
            "reminder_due",
            {"reminder_id": "r1", "priority": "normal", "speak_text": "该喝水啦"},
        ),
        "evt-rem-normal",
    )
    normal_action = action_from(normal_sent[0])
    assert normal_action["delivery"] == "after_current"
    assert normal.client_session_delegate.signals == []

    high, high_sent = make_stream()
    high._handle_client_event(
        event_payload(
            "reminder_due",
            {"reminder_id": "r2", "priority": "high", "speak_text": "该吃药啦"},
        ),
        "evt-rem-high",
    )
    high_action = action_from(high_sent[0])
    assert high_action["delivery"] == "interrupt"
    assert high.client_session_delegate.signals[0].type == ChatSignalType.INTERRUPT


def test_exit_hint_semantic_call_does_not_block_data_channel_thread():
    stream, _ = make_stream()
    completed = Event()

    def slow_non_exit(_text):
        time.sleep(0.2)
        completed.set()
        return False, 1.0

    stream.session_event_policy._semantics.classify_exit_intent = slow_non_exit
    start = time.perf_counter()
    stream._handle_client_event(
        event_payload("user_exit_hint", {"text": "我先休息了"}),
        "evt-async-exit",
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 0.1
    assert completed.wait(timeout=1.0)


def test_exit_hint_result_ends_only_if_no_new_user_activity():
    stream, sent = make_stream(exit_intent_confidence=0.8)
    future = Future()
    future.set_result((True, 0.95))
    policy = stream.session_event_policy
    policy._finish_user_exit_hint(
        future, "evt-exit", policy._user_activity_generation
    )

    assert action_from(sent[0])["then"] == "end"
    assert action_from(sent[0])["reason"] == "user_farewell"
    assert stream.client_session_delegate.signals[0].type == ChatSignalType.INTERRUPT

    stale, stale_sent = make_stream(exit_intent_confidence=0.8)
    generation = stale.session_event_policy._user_activity_generation
    stale.note_user_activity("new_turn")
    stale.session_event_policy._finish_user_exit_hint(future, "evt-stale", generation)
    assert stale_sent == []


def test_data_channel_dispatches_client_event_to_client_action():
    stream = RtcStream(session_id="test-channel", stream_start_delay=0)
    stream.client_session_delegate = DummyDelegate()

    class FakeChannel:
        def __init__(self):
            self.callbacks = {}
            self.sent = []

        def on(self, name):
            def register(callback):
                self.callbacks[name] = callback
                return callback
            return register

        def send(self, payload):
            self.sent.append(json.loads(payload))

    channel = FakeChannel()
    stream.set_channel(channel)
    channel.callbacks["message"](json.dumps({
        "header": {"name": "ClientEvent", "request_id": "evt-channel"},
        "payload": event_payload("user_silence", {"level": 1, "silence_ms": 60000}),
    }))
    assert action_from(channel.sent[0])["reason"] == "user_silence_level1"

    channel.callbacks["message"](json.dumps({
        "header": {"name": "ClientEvent", "request_id": "evt-invalid"},
        "payload": {"type": "wake", "data": {}},
    }))
    assert channel.sent[-1]["header"]["name"] == "Error"
    assert channel.sent[-1]["payload"]["code"] == "INVALID_CLIENT_EVENT"


def test_policy_llm_json_parsing_for_exit_and_reminder_without_network():
    stream, _ = make_stream()
    responses = iter([
        "```json\n{\"is_exit\": true, \"confidence\": 0.93}\n```",
        json.dumps({
            "title": "吃药",
            "remind_at": int(time.time() * 1000) + 60000,
            "repeat": "none",
            "speak_text": "该吃药啦",
            "confidence": 0.9,
        }),
    ])

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))]
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())
            self.closed = False

        def close(self):
            self.closed = True

    clients = []
    def new_client():
        client = FakeClient()
        clients.append(client)
        return client

    resolver = stream.session_event_policy._semantics
    resolver._new_client = new_client
    assert resolver.classify_exit_intent("我先睡了") == (True, 0.93)
    reminder = resolver.extract_reminder("一分钟后提醒我吃药")
    assert reminder["title"] == "吃药"
    assert reminder["kind"] == "custom"
    assert all(client.closed for client in clients)


def test_stale_event_and_unknown_event_have_no_side_effect():
    stream, sent = make_stream(event_max_age_ms=1000)
    stream._handle_client_event(
        event_payload("ui_end", ts=int(time.time() * 1000) - 2000), "evt-stale"
    )
    stream._handle_client_event(event_payload("future_extension"), "evt-unknown")
    assert sent == []
    assert stream._session_ending is False


def test_policy_configuration_is_preserved_when_fastrtc_copies_stream():
    stream, _ = make_stream(silence_level1_ms=12345)
    stream.client_handler_delegate = object()
    copied = stream.copy()
    assert copied.session_policy_config["silence_level1_ms"] == 12345


def test_device_info_parses_elder_profile(monkeypatch):
    monkeypatch.setenv("V1_PERSONA_RUNTIME_ENABLED", "0")
    stream, sent = make_stream()

    stream._handle_device_info(
        {
            "device_sn": "speaker-1",
            "elder_profile": {
                "nickname": " 王奶奶 ",
                "gender": " 女 ",
                "age": 78,
                "native_place": " 四川成都 ",
                "ignored": "value",
            },
        },
        "device-info-1",
    )

    expected = {
        "nickname": "王奶奶",
        "gender": "女",
        "age": 78,
        "native_place": "四川成都",
    }
    assert stream.client_session_delegate.device_info["elder_profile"] == expected
    assert stream.client_session_delegate.shared_states.device_info["elder_profile"] == expected
    assert sent[-1]["name"] == "DeviceInfoAck"
