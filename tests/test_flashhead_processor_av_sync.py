import queue
import threading

import numpy as np

from handlers.avatar.flashhead.flashhead_processor import (
    FlashHeadProcessor,
    FlashHeadProcessorCallbacks,
    FrameQueueItem,
)


def test_collector_pairs_queued_idle_animation_frame_with_silent_audio():
    processor = object.__new__(FlashHeadProcessor)
    processor.tgt_fps = 1000
    processor._stop_event = threading.Event()
    processor._output_queue = queue.Queue()
    processor._interrupted = False
    processor._idle_frame = np.full((2, 2, 3), 7, dtype=np.uint8)
    processor._original_audio_per_frame = 960
    processor._output_sr = 24000
    processor._av_diag_collect_seq = 0
    processor._av_diag_audio_samples_out = 0

    queued_frame = np.full((2, 2, 3), 3, dtype=np.uint8)
    processor._output_queue.put(
        FrameQueueItem(
            video_frame=queued_frame,
            audio_segment=None,
            speech_id=None,
            end_of_speech=False,
        )
    )

    video_frames = []
    video_speech_ids = []
    audio_frames = []
    audio_speech_ids = []

    def on_video_frame(frame, speech_id=None):
        video_frames.append(frame.copy())
        video_speech_ids.append(speech_id)

    def on_audio_frame(audio, speech_id=None):
        audio_frames.append(audio.copy())
        audio_speech_ids.append(speech_id)
        processor._stop_event.set()

    processor.callbacks = FlashHeadProcessorCallbacks(
        on_video_frame=on_video_frame,
        on_audio_frame=on_audio_frame,
    )

    processor._frame_collector_worker()

    assert len(video_frames) == 1
    np.testing.assert_array_equal(video_frames[0], queued_frame)
    assert video_speech_ids == [None]
    assert len(audio_frames) == 1
    assert audio_speech_ids == [None]
    assert audio_frames[0].shape == (960,)
    assert audio_frames[0].dtype == np.float32
    np.testing.assert_array_equal(audio_frames[0], np.zeros(960, dtype=np.float32))
