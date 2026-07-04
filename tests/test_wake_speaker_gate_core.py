from handlers.voice_gate.text_utils import (
    is_wake_only_text,
    looks_like_asr_noise,
    strip_wake_word,
)
from handlers.voice_gate.voice_gate import (
    VoiceFeatures,
    VoiceGateConfig,
    VoiceSessionGate,
)


def _features(embedding):
    return VoiceFeatures(
        duration_ms=900,
        rms=0.04,
        snr_db=18.0,
        embedding=tuple(embedding),
    )


def test_voice_gate_locks_wake_speaker_and_rejects_other_voice():
    gate = VoiceSessionGate(VoiceGateConfig(speaker_threshold=0.70))

    wake = gate.on_wake(_features((1.0, 0.0, 0.0)), now_ms=1000)
    target = gate.handle_segment(
        _features((0.92, 0.10, 0.0)),
        "我想出去玩儿",
        now_ms=2000,
        tts_playing=False,
    )
    other = gate.handle_segment(
        _features((0.0, 1.0, 0.0)),
        "旁边的人在聊天",
        now_ms=3000,
        tts_playing=False,
    )

    assert wake.accepted is True
    assert wake.reason == "wake_locked"
    assert target.accepted is True
    assert target.reason == "accepted"
    assert other.accepted is False
    assert other.reason == "speaker_reject"


def test_wake_word_text_utils_handle_wake_only_and_inline_command():
    assert is_wake_only_text("小伴小伴", "小伴小伴") is True
    assert is_wake_only_text("小伴小伴 今天天气怎么样", "小伴小伴") is False
    assert strip_wake_word("小伴小伴 今天天气怎么样", "小伴小伴") == "今天天气怎么样"
    assert looks_like_asr_noise("", "小伴小伴") is True
