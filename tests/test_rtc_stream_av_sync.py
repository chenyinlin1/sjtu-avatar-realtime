import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from chat_engine.data_models.engine_channel_type import EngineChannelType
from service.rtc_service.rtc_stream import RtcStream


class _Bundle:
    def __init__(self, data, metadata=None):
        self._data = data
        self.metadata = metadata or {}

    def get_main_data(self):
        return self._data


class _Delegate:
    def __init__(self):
        self.queues = {
            EngineChannelType.AUDIO: asyncio.Queue(),
            EngineChannelType.VIDEO: asyncio.Queue(),
        }

    async def get_data(self, modality, timeout=0.1):
        return await self.queues[modality].get()

    def clear_data(self):
        cleared = {}
        for modality, data_queue in self.queues.items():
            count = 0
            while not data_queue.empty():
                data_queue.get_nowait()
                count += 1
            cleared[modality.value] = count
        return cleared

    def put(self, modality, data):
        self.queues[modality].put_nowait(data)


def _chat_data(array, speech_id=None):
    metadata = {}
    if speech_id is not None:
        metadata["flashhead_speech_id"] = speech_id
    return SimpleNamespace(
        data=_Bundle(array, metadata=metadata),
        timestamp=(0, 16000),
        is_first_data=False,
        is_last_data=False,
        stream_id=None,
    )


def _stream_with_delegate():
    stream = RtcStream(session_id="test-session", output_sample_rate=24000, fps=25)
    delegate = _Delegate()
    stream.client_session_delegate = delegate
    stream.first_audio_emitted = True
    return stream, delegate


@pytest.mark.asyncio
async def test_emit_accumulates_audio_sync_samples():
    stream, delegate = _stream_with_delegate()
    audio = np.zeros((1, 960), dtype=np.float32)
    delegate.put(EngineChannelType.AUDIO, _chat_data(audio))

    sample_rate, emitted_audio = await asyncio.wait_for(stream.emit(), timeout=1.0)

    assert sample_rate == 24000
    np.testing.assert_array_equal(emitted_audio, audio)
    assert stream._av_sync_audio_samples_total == 960


@pytest.mark.asyncio
async def test_video_emit_does_not_wait_when_audio_is_within_lead_limit():
    stream, delegate = _stream_with_delegate()
    stream._av_sync_video_frames_total = 4  # next frame is 200ms at 25fps
    stream._av_sync_audio_samples_total = 480  # 20ms audio, so drift is exactly 180ms
    video = np.zeros((2, 2, 3), dtype=np.uint8)
    delegate.put(EngineChannelType.VIDEO, _chat_data(video))

    emitted_video = await asyncio.wait_for(stream.video_emit(), timeout=0.2)

    np.testing.assert_array_equal(emitted_video, video)
    assert stream._av_sync_video_frames_total == 5


@pytest.mark.asyncio
async def test_video_emit_waits_until_audio_catches_up():
    stream, delegate = _stream_with_delegate()
    stream._av_sync_video_frames_total = 4  # next frame would be 200ms
    stream._av_sync_audio_samples_total = 0
    video = np.zeros((2, 2, 3), dtype=np.uint8)
    delegate.put(EngineChannelType.VIDEO, _chat_data(video))

    task = asyncio.create_task(stream.video_emit())
    await asyncio.sleep(0.02)

    assert not task.done()

    stream._av_sync_audio_samples_total = 480  # 20ms audio makes drift 180ms
    emitted_video = await asyncio.wait_for(task, timeout=1.0)

    np.testing.assert_array_equal(emitted_video, video)
    assert stream._av_sync_video_frames_total == 5


@pytest.mark.asyncio
async def test_reset_clears_stale_queues_and_gates_video_until_new_audio():
    stream, delegate = _stream_with_delegate()
    stream._av_sync_audio_samples_total = 960
    stream._av_sync_video_frames_total = 10

    stale_audio = np.ones((1, 960), dtype=np.float32)
    stale_video = np.ones((2, 2, 3), dtype=np.uint8)
    delegate.put(EngineChannelType.AUDIO, _chat_data(stale_audio))
    delegate.put(EngineChannelType.VIDEO, _chat_data(stale_video))

    stream.request_av_sync_reset(
        "client_playback_begin",
        target_speech_id="speech-1",
        wait_for_audio=True,
    )
    video_task = asyncio.create_task(stream.video_emit())
    await asyncio.sleep(0.02)

    assert not video_task.done()
    assert delegate.queues[EngineChannelType.AUDIO].empty()
    assert delegate.queues[EngineChannelType.VIDEO].empty()
    assert stream._av_sync_audio_samples_total == 0
    assert stream._av_sync_video_frames_total == 0

    new_video = np.full((2, 2, 3), 3, dtype=np.uint8)
    delegate.put(EngineChannelType.VIDEO, _chat_data(new_video, speech_id="speech-1"))
    await asyncio.sleep(0.02)
    assert not video_task.done()

    new_audio = np.full((1, 960), 0.5, dtype=np.float32)
    delegate.put(EngineChannelType.AUDIO, _chat_data(new_audio, speech_id="speech-1"))
    sample_rate, emitted_audio = await asyncio.wait_for(stream.emit(), timeout=1.0)
    emitted_video = await asyncio.wait_for(video_task, timeout=1.0)

    assert sample_rate == 24000
    np.testing.assert_array_equal(emitted_audio, new_audio)
    np.testing.assert_array_equal(emitted_video, new_video)
    assert stream._av_sync_audio_samples_total == 960
    assert stream._av_sync_video_frames_total == 1
    assert not stream._av_sync_wait_for_first_audio

@pytest.mark.asyncio
async def test_targeted_reset_ignores_idle_packets_until_matching_audio():
    stream, delegate = _stream_with_delegate()
    stream.request_av_sync_reset(
        "client_playback_begin",
        target_speech_id="speech-1",
        wait_for_audio=True,
    )

    video_task = asyncio.create_task(stream.video_emit())
    audio_task = asyncio.create_task(stream.emit())
    await asyncio.sleep(0.02)

    idle_audio = np.zeros((1, 960), dtype=np.float32)
    idle_video = np.zeros((2, 2, 3), dtype=np.uint8)
    delegate.put(EngineChannelType.AUDIO, _chat_data(idle_audio))
    delegate.put(EngineChannelType.VIDEO, _chat_data(idle_video))
    await asyncio.sleep(0.05)

    assert not audio_task.done()
    assert not video_task.done()
    assert stream._av_sync_audio_samples_total == 0
    assert stream._av_sync_video_frames_total == 0
    assert stream._av_sync_wait_for_first_audio

    target_video = np.full((2, 2, 3), 4, dtype=np.uint8)
    target_audio = np.full((1, 960), 0.75, dtype=np.float32)
    delegate.put(EngineChannelType.VIDEO, _chat_data(target_video, speech_id="speech-1"))
    delegate.put(EngineChannelType.AUDIO, _chat_data(target_audio, speech_id="speech-1"))

    sample_rate, emitted_audio = await asyncio.wait_for(audio_task, timeout=1.0)
    emitted_video = await asyncio.wait_for(video_task, timeout=1.0)

    assert sample_rate == 24000
    np.testing.assert_array_equal(emitted_audio, target_audio)
    np.testing.assert_array_equal(emitted_video, target_video)
    assert stream._av_sync_audio_samples_total == 960
    assert stream._av_sync_video_frames_total == 1
    assert not stream._av_sync_wait_for_first_audio
