import pytest

from service.rtc_service.av_sync import VideoCatchupConfig, VideoCatchupController


def make_controller(**config_overrides) -> VideoCatchupController:
    config = VideoCatchupConfig(**config_overrides)
    return VideoCatchupController(fps=25, config=config)


def test_sustained_audio_lead_schedules_bounded_frame_drop():
    controller = make_controller()

    assert controller.observe(-1800.0, queue_size=91, now_s=0.00) is None
    assert controller.observe(-1800.0, queue_size=91, now_s=0.04) is None
    plan = controller.observe(-1800.0, queue_size=91, now_s=0.08)

    assert plan is not None
    assert plan.observed_lag_ms == pytest.approx(1800.0)
    assert plan.requested_drop_frames == 43
    assert plan.queue_size == 91


def test_short_drift_spike_does_not_schedule_catchup():
    controller = make_controller()

    assert controller.observe(-260.0, queue_size=20, now_s=0.00) is None
    assert controller.observe(-80.0, queue_size=20, now_s=0.04) is None
    assert controller.observe(-260.0, queue_size=20, now_s=0.08) is None
    assert controller.observe(-260.0, queue_size=20, now_s=0.12) is None


def test_drop_plan_always_retains_recent_video_frames():
    controller = make_controller(consecutive_samples=1, retain_frames=2)

    plan = controller.observe(-5000.0, queue_size=10, now_s=0.0)

    assert plan is not None
    assert plan.requested_drop_frames == 8


def test_cooldown_blocks_repeated_catchup():
    controller = make_controller(consecutive_samples=1, cooldown_ms=500.0)

    assert controller.observe(-1000.0, queue_size=30, now_s=0.0) is not None
    assert controller.observe(-1000.0, queue_size=30, now_s=0.1) is None
    assert controller.observe(-1000.0, queue_size=30, now_s=0.6) is not None


def test_reset_clears_cooldown_and_detection_streak():
    controller = make_controller(consecutive_samples=1, cooldown_ms=500.0)
    assert controller.observe(-1000.0, queue_size=30, now_s=0.0) is not None

    controller.reset()

    assert controller.observe(-1000.0, queue_size=30, now_s=0.1) is not None
