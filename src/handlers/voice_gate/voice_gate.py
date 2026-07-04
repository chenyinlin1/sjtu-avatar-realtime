from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from handlers.voice_gate.text_utils import normalize_text


class VoiceSessionState(StrEnum):
    STANDBY = "standby"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True, slots=True)
class VoiceGateConfig:
    min_duration_ms: int = 300
    min_rms: float = 0.01
    min_snr_db: float = 8.0
    speaker_threshold: float = 0.45
    speaker_borderline_threshold: float = 0.40
    speaker_soft_threshold: float = 0.45
    trusted_speaker_profile_threshold: float = 0.55
    rejected_speaker_learn_max_score: float = 0.35
    rejected_speaker_profile_threshold: float = 0.85
    rejected_speaker_margin: float = 0.08
    rejected_speaker_profile_max: int = 6
    speaker_grace_ms: int = 12_000
    speaker_grace_segments: int = 2
    idle_timeout_ms: int = 90_000
    non_target_timeout_ms: int = 20_000


@dataclass(frozen=True, slots=True)
class VoiceFeatures:
    duration_ms: int
    rms: float
    snr_db: float
    embedding: tuple[float, ...]
    is_echo: bool = False


@dataclass(frozen=True, slots=True)
class VoiceDecision:
    accepted: bool
    reason: str
    state: VoiceSessionState
    send_to_asr: bool = False
    allow_interrupt: bool = False
    speaker_score: float | None = None
    rejected_speaker_score: float | None = None
    semantic_addressed: bool = False


class VoiceSessionGate:
    def __init__(self, config: VoiceGateConfig | None = None):
        self.config = config or VoiceGateConfig()
        self.state = VoiceSessionState.STANDBY
        self.active_speaker: tuple[float, ...] | None = None
        self.trusted_speaker_profiles: list[tuple[float, ...]] = []
        self.rejected_speaker_profiles: list[tuple[float, ...]] = []
        self.last_activity_ms: int | None = None
        self.non_target_speech_ms = 0
        self.speaker_grace_until_ms: int | None = None
        self.speaker_grace_segments_remaining = 0

    def on_wake(self, features: VoiceFeatures, now_ms: int) -> VoiceDecision:
        if not _has_embedding(features.embedding):
            return self._decision(False, "missing_embedding")

        self.active_speaker = _normalize_embedding(features.embedding)
        self.trusted_speaker_profiles = [self.active_speaker]
        self.rejected_speaker_profiles = []
        self.state = VoiceSessionState.LISTENING
        self.last_activity_ms = now_ms
        self.non_target_speech_ms = 0
        self.speaker_grace_until_ms = now_ms + self.config.speaker_grace_ms
        self.speaker_grace_segments_remaining = self.config.speaker_grace_segments
        return self._decision(True, "wake_locked", send_to_asr=True, speaker_score=1.0)

    def handle_segment(
        self,
        features: VoiceFeatures,
        text: str,
        now_ms: int,
        *,
        tts_playing: bool = False,
        speaker_gate_enabled: bool = True,
    ) -> VoiceDecision:
        if self.state == VoiceSessionState.STANDBY or self.active_speaker is None:
            return self._decision(False, "standby")

        reject_reason = self._quality_reject_reason(features)
        if reject_reason is not None:
            return self._decision(False, reject_reason)

        speaker_score = self.speaker_score(features.embedding)
        rejected_speaker_score = self.rejected_speaker_score(features.embedding)
        known_rejected_speaker = self.is_known_rejected_speaker(
            features.embedding,
            speaker_score=speaker_score,
            rejected_speaker_score=rejected_speaker_score,
        )
        speaker_grace = (
            self._can_use_speaker_grace(speaker_score, now_ms)
            and not known_rejected_speaker
        )
        speaker_matches = (
            speaker_score >= self.config.speaker_threshold
            and not known_rejected_speaker
        )
        if speaker_gate_enabled and not speaker_matches and not speaker_grace:
            self.remember_rejected_speaker(features.embedding, speaker_score=speaker_score)
            self.non_target_speech_ms += max(features.duration_ms, 0)
            if self._has_wake_grace_window(now_ms):
                self._consume_wake_grace_segment()
            if self.non_target_speech_ms >= self.config.non_target_timeout_ms:
                self.reset()
                return self._decision(
                    False,
                    "non_target_timeout",
                    speaker_score=speaker_score,
                    rejected_speaker_score=rejected_speaker_score,
                )
            return self._decision(
                False,
                "speaker_reject",
                speaker_score=speaker_score,
                rejected_speaker_score=rejected_speaker_score,
            )

        self.non_target_speech_ms = 0
        semantic_addressed = bool(normalize_text(text))
        allow_interrupt = tts_playing and semantic_addressed

        if not semantic_addressed:
            return self._decision(
                False,
                "not_addressed",
                send_to_asr=True,
                speaker_score=speaker_score,
                rejected_speaker_score=rejected_speaker_score,
                semantic_addressed=False,
            )

        self.last_activity_ms = now_ms
        if speaker_gate_enabled and speaker_grace:
            self._consume_wake_grace_segment()
        reason = "interrupt" if allow_interrupt else "accepted"
        return self._decision(
            True,
            reason,
            send_to_asr=True,
            allow_interrupt=allow_interrupt,
            speaker_score=speaker_score,
            rejected_speaker_score=rejected_speaker_score,
            semantic_addressed=True,
        )

    def check_timeouts(self, now_ms: int) -> VoiceDecision:
        if self.state == VoiceSessionState.STANDBY or self.last_activity_ms is None:
            return self._decision(False, "standby")

        if now_ms - self.last_activity_ms > self.config.idle_timeout_ms:
            self.reset()
            return self._decision(False, "idle_timeout")

        return self._decision(False, "active")

    def quality_reject_reason(self, features: VoiceFeatures) -> str | None:
        return self._quality_reject_reason(features)

    def can_try_asr_for_speaker_score(
        self,
        speaker_score: float | None,
        now_ms: int,
        *,
        speaker_gate_enabled: bool = True,
    ) -> bool:
        if not speaker_gate_enabled:
            return True
        if speaker_score is None:
            return True
        return (
            speaker_score >= self.config.speaker_threshold
            or speaker_score >= self.config.speaker_borderline_threshold
            or self._can_use_speaker_grace(speaker_score, now_ms)
        )

    def speaker_score(self, embedding: tuple[float, ...]) -> float:
        if not _has_embedding(embedding):
            return 0.0
        profiles = self._speaker_profiles()
        if not profiles:
            return 0.0
        return max(_cosine_similarity(profile, embedding) for profile in profiles)

    def rejected_speaker_score(self, embedding: tuple[float, ...]) -> float:
        if not _has_embedding(embedding) or not self.rejected_speaker_profiles:
            return 0.0
        return max(_cosine_similarity(profile, embedding) for profile in self.rejected_speaker_profiles)

    def is_known_rejected_speaker(
        self,
        embedding: tuple[float, ...],
        *,
        speaker_score: float | None = None,
        rejected_speaker_score: float | None = None,
    ) -> bool:
        if not _has_embedding(embedding):
            return False
        target_score = self.speaker_score(embedding) if speaker_score is None else speaker_score
        negative_score = (
            self.rejected_speaker_score(embedding)
            if rejected_speaker_score is None
            else rejected_speaker_score
        )
        if negative_score < self.config.rejected_speaker_profile_threshold:
            return False
        if target_score >= self.config.trusted_speaker_profile_threshold:
            return negative_score >= target_score + self.config.rejected_speaker_margin
        return negative_score >= target_score + self.config.rejected_speaker_margin

    def remember_rejected_speaker(
        self,
        embedding: tuple[float, ...],
        *,
        speaker_score: float | None = None,
    ) -> bool:
        if self.active_speaker is None or not _has_embedding(embedding):
            return False
        score = self.speaker_score(embedding) if speaker_score is None else speaker_score
        if score >= self.config.rejected_speaker_learn_max_score:
            return False
        if score >= self.config.trusted_speaker_profile_threshold:
            return False
        normalized = _normalize_embedding(embedding)
        if any(_cosine_similarity(profile, normalized) >= 0.98 for profile in self.rejected_speaker_profiles):
            return False
        self.rejected_speaker_profiles.append(normalized)
        overflow = len(self.rejected_speaker_profiles) - max(self.config.rejected_speaker_profile_max, 1)
        if overflow > 0:
            del self.rejected_speaker_profiles[:overflow]
        return True

    def reset(self) -> None:
        self.state = VoiceSessionState.STANDBY
        self.active_speaker = None
        self.trusted_speaker_profiles = []
        self.rejected_speaker_profiles = []
        self.last_activity_ms = None
        self.non_target_speech_ms = 0
        self.speaker_grace_until_ms = None
        self.speaker_grace_segments_remaining = 0

    def _quality_reject_reason(self, features: VoiceFeatures) -> str | None:
        if features.duration_ms < self.config.min_duration_ms:
            return "too_short"
        if features.rms < self.config.min_rms:
            return "too_quiet"
        if features.snr_db < self.config.min_snr_db:
            return "low_snr"
        if features.is_echo:
            return "echo_reject"
        if not _has_embedding(features.embedding):
            return "missing_embedding"
        return None

    def _can_use_speaker_grace(self, speaker_score: float, now_ms: int) -> bool:
        return (
            self._has_wake_grace_window(now_ms)
            and speaker_score >= self.config.speaker_soft_threshold
        )

    def _has_wake_grace_window(self, now_ms: int) -> bool:
        return (
            self.speaker_grace_until_ms is not None
            and self.speaker_grace_segments_remaining > 0
            and now_ms <= self.speaker_grace_until_ms
        )

    def _consume_wake_grace_segment(self) -> None:
        if self.speaker_grace_segments_remaining > 0:
            self.speaker_grace_segments_remaining -= 1

    def _speaker_profiles(self) -> list[tuple[float, ...]]:
        if self.trusted_speaker_profiles:
            return self.trusted_speaker_profiles
        return [self.active_speaker] if self.active_speaker is not None else []

    def _decision(
        self,
        accepted: bool,
        reason: str,
        *,
        send_to_asr: bool = False,
        allow_interrupt: bool = False,
        speaker_score: float | None = None,
        rejected_speaker_score: float | None = None,
        semantic_addressed: bool = False,
    ) -> VoiceDecision:
        return VoiceDecision(
            accepted=accepted,
            reason=reason,
            state=self.state,
            send_to_asr=send_to_asr,
            allow_interrupt=allow_interrupt,
            speaker_score=speaker_score,
            rejected_speaker_score=rejected_speaker_score,
            semantic_addressed=semantic_addressed,
        )


def _has_embedding(embedding: tuple[float, ...]) -> bool:
    return any(abs(value) > 1e-9 for value in embedding)


def _normalize_embedding(embedding: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in embedding))
    if norm == 0:
        return embedding
    return tuple(value / norm for value in embedding)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return 0.0

    length = min(len(left), len(right))
    left_part = left[:length]
    right_part = right[:length]
    left_norm = math.sqrt(sum(value * value for value in left_part))
    right_norm = math.sqrt(sum(value * value for value in right_part))
    if left_norm == 0 or right_norm == 0:
        return 0.0

    dot = sum(left_part[index] * right_part[index] for index in range(length))
    return dot / (left_norm * right_norm)
