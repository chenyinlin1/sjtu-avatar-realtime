import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _TestLogger:
    def warning(self, *args, **kwargs):
        pass


sys.modules.setdefault("loguru", SimpleNamespace(logger=_TestLogger()))

from engine_utils.conversation_audit_logger import audit_event, flush_audit_events


def test_conversation_audit_jsonl_contains_identity_and_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAVATAR_CONVERSATION_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAVATAR_CONVERSATION_AUDIT_MAX_TEXT_CHARS", "64")

    shared_states = SimpleNamespace(
        device_info={
            "device_sn": "speaker-001",
            "elder_id": "elder-42",
            "tenant_id": "tenant-a",
            "persona_id": "role-fallback",
        },
        persona_runtime={
            "persona_id": "role-7",
            "elder_id": "elder-42",
            "tenant_id": "tenant-a",
            "relationship": "granddaughter",
            "display_name": "Xiao Mei",
            "persona_system_prompt": "role prompt",
        },
        client_endpoint="ws",
    )
    context = SimpleNamespace(
        session_id="session-a",
        shared_states=shared_states,
        stream_manager=None,
    )

    turn_id = audit_event(
        context,
        "asr_transcript",
        stream_key="stream-human",
        create_turn=True,
        transcript="hello",
    )
    audit_event(
        context,
        "llm_input",
        stream_key="stream-llm",
        bind_stream_key="stream-avatar",
        turn_id=turn_id,
        messages=[{"role": "user", "content": "x" * 128}],
    )
    flush_audit_events(timeout=2.0)

    files = list(tmp_path.glob("conversation_audit_*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]

    assert [record["event"] for record in records] == ["asr_transcript", "llm_input"]
    assert records[0]["turn_id"] == turn_id
    assert records[1]["turn_id"] == turn_id
    identity = records[0]["identity"]
    assert identity["person_id"] == "elder-42"
    assert identity["elder_id"] == "elder-42"
    assert identity["role_id"] == "role-7"
    assert identity["role_info"]["display_name"] == "Xiao Mei"
    assert identity["is_speaker_endpoint_guess"] is True
    assert "truncated" in records[1]["payload"]["messages"][0]["content"]
