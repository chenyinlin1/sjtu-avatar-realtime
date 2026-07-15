"""Policy for catching video up when audio remains ahead.

The controller is intentionally independent from RTC queues and encoders.  It
observes RTP drift and returns a plan; the stream layer decides how to execute
that plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VideoCatchupConfig:
    """Thresholds for sustained audio-lead detection."""

    trigger_lag_ms: float = 200.0
    target_lag_ms: float = 80.0
    consecutive_samples: int = 3
    cooldown_ms: float = 500.0
    retain_frames: int = 2

    def __post_init__(self) -> None:
        if self.trigger_lag_ms <= self.target_lag_ms:
            raise ValueError("trigger_lag_ms must be greater than target_lag_ms")
        if self.target_lag_ms < 0:
            raise ValueError("target_lag_ms must be non-negative")
        if self.consecutive_samples < 1:
            raise ValueError("consecutive_samples must be at least 1")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms must be non-negative")
        if self.retain_frames < 1:
            raise ValueError("retain_frames must be at least 1")


@dataclass(frozen=True)
class VideoCatchupPlan:
    """A bounded request to skip stale video before the next RTP frame."""

    observed_lag_ms: float
    requested_drop_frames: int
    queue_size: int


class VideoCatchupController:
    """Detect sustained audio lead and calculate a bounded catch-up plan."""

    def __init__(
        self,
        fps: float,
        config: Optional[VideoCatchupConfig] = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.config = config or VideoCatchupConfig()
        self.frame_interval_ms = 1000.0 / fps
        self._consecutive_lag_samples = 0
        self._last_trigger_s: Optional[float] = None

    def reset(self) -> None:
        """Forget observations when a playback stream establishes a new base."""
        self._consecutive_lag_samples = 0
        self._last_trigger_s = None

    def observe(
        self,
        drift_ms: float,
        *,
        queue_size: int,
        now_s: float,
    ) -> Optional[VideoCatchupPlan]:
        """Return a plan after audio lead is sustained for the configured window.

        ``drift_ms`` is video RTP time minus audio RTP time.  A negative value
        therefore means audio is ahead.
        """
        if not math.isfinite(drift_ms):
            self._consecutive_lag_samples = 0
            return None

        audio_lead_ms = -drift_ms
        if audio_lead_ms <= self.config.trigger_lag_ms:
            self._consecutive_lag_samples = 0
            return None

        self._consecutive_lag_samples += 1
        if self._consecutive_lag_samples < self.config.consecutive_samples:
            return None
        self._consecutive_lag_samples = 0

        if self._cooldown_active(now_s):
            return None

        desired_drop_frames = math.ceil(
            (audio_lead_ms - self.config.target_lag_ms) / self.frame_interval_ms
        )
        available_drop_frames = max(0, int(queue_size) - self.config.retain_frames)
        requested_drop_frames = min(desired_drop_frames, available_drop_frames)
        self._last_trigger_s = now_s
        return VideoCatchupPlan(
            observed_lag_ms=audio_lead_ms,
            requested_drop_frames=requested_drop_frames,
            queue_size=max(0, int(queue_size)),
        )

    def _cooldown_active(self, now_s: float) -> bool:
        if self._last_trigger_s is None:
            return False
        elapsed_ms = (now_s - self._last_trigger_s) * 1000.0
        return elapsed_ms < self.config.cooldown_ms
