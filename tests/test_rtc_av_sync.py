import asyncio

import pytest

from service.rtc_service.rtc_stream import RtcStream


class DummyDelegate:
    def clear_data(self):
        return {"audio": 0, "video": 0, "text": 0}


def make_stream() -> RtcStream:
    stream = RtcStream(
        session_id="av-sync-test",
        fps=25,
        stream_start_delay=0,
    )
    stream.client_session_delegate = DummyDelegate()
    stream._clear_queue = lambda: None
    return stream


def arm_reset(stream: RtcStream, reason: str = "test-reset") -> None:
    stream.request_av_sync_reset(reason=reason)
    stream._apply_pending_av_sync_reset()


def test_post_reset_video_is_rebased_to_audio_frame_boundary():
    stream = make_stream()
    arm_reset(stream)
    stream.note_audio_rtp_egress(9540.0, codec="audio/opus")

    aligned_ms = asyncio.run(
        stream.wait_for_video_rtp_egress(7960.0, codec="video/H264")
    )

    assert aligned_ms == pytest.approx(9560.0)
    assert stream._av_rtp_video_offset_ms == pytest.approx(1600.0)
    assert aligned_ms - stream._av_rtp_audio_ms == pytest.approx(20.0)
    assert stream._av_rtp_video_rebase_pending is False


def test_rebase_offset_persists_and_keeps_video_timestamps_monotonic():
    stream = make_stream()
    arm_reset(stream)
    stream.note_audio_rtp_egress(9540.0, codec="audio/opus")

    first_ms = asyncio.run(
        stream.wait_for_video_rtp_egress(7960.0, codec="video/H264")
    )
    stream.note_audio_rtp_egress(9580.0, codec="audio/opus")
    second_ms = asyncio.run(
        stream.wait_for_video_rtp_egress(8000.0, codec="video/H264")
    )

    assert first_ms == pytest.approx(9560.0)
    assert second_ms == pytest.approx(9600.0)
    assert second_ms - first_ms == pytest.approx(40.0)
    assert stream._av_rtp_video_offset_ms == pytest.approx(1600.0)


def test_video_is_not_rebased_without_a_reset():
    stream = make_stream()
    stream.note_audio_rtp_egress(9540.0, codec="audio/opus")

    aligned_ms = asyncio.run(
        stream.wait_for_video_rtp_egress(7960.0, codec="video/VP8")
    )

    assert aligned_ms == pytest.approx(7960.0)
    assert stream._av_rtp_video_offset_ms == pytest.approx(0.0)


def test_small_post_reset_jitter_does_not_trigger_rebase():
    stream = make_stream()
    arm_reset(stream)
    stream.note_audio_rtp_egress(9540.0, codec="audio/opus")

    aligned_ms = asyncio.run(
        stream.wait_for_video_rtp_egress(9480.0, codec="video/H264")
    )

    assert aligned_ms == pytest.approx(9480.0)
    assert stream._av_rtp_video_offset_ms == pytest.approx(0.0)
    assert stream._av_rtp_video_rebase_pending is False


def test_existing_video_lead_limit_still_waits_for_audio():
    stream = make_stream()
    stream.note_audio_rtp_egress(1000.0, codec="audio/opus")

    async def exercise_wait():
        task = asyncio.create_task(
            stream.wait_for_video_rtp_egress(1080.0, codec="video/H264")
        )
        await asyncio.sleep(0.015)
        assert task.done() is False
        stream.note_audio_rtp_egress(1040.0, codec="audio/opus")
        return await asyncio.wait_for(task, timeout=0.1)

    assert asyncio.run(exercise_wait()) == pytest.approx(1080.0)
