import queue
import threading

import numpy as np

from chat_engine.core.stream_manager import ChatStreamer, InputStreamStats
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from handlers.avatar.flashhead.flashhead_processor import (
    FlashHeadProcessor,
    FlashHeadProcessorCallbacks,
    FrameQueueItem,
)


def _make_collector_processor(callbacks):
    processor = object.__new__(FlashHeadProcessor)
    processor.callbacks = callbacks
    processor.tgt_fps = 200
    processor._stop_event = threading.Event()
    processor._output_queue = queue.Queue()
    processor._interrupted = False
    processor._idle_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    processor._original_audio_per_frame = 1
    processor._output_sr = 1
    processor._av_diag_collect_seq = 0
    processor._av_diag_audio_samples_out = 0
    return processor


def _speech_frame(speech_id):
    return FrameQueueItem(
        video_frame=np.zeros((2, 2, 3), dtype=np.uint8),
        audio_segment=np.zeros(1, dtype=np.float32),
        speech_id=speech_id,
        end_of_speech=False,
    )


def test_collector_continues_after_idle_callback_loses_stream_cancel_race():
    audio_attempts = []
    second_frame_emitted = threading.Event()
    processor = None

    def on_audio_frame(_audio, speech_id):
        audio_attempts.append(speech_id)
        if len(audio_attempts) == 1:
            raise ValueError("No current stream")
        second_frame_emitted.set()
        processor._stop_event.set()

    processor = _make_collector_processor(
        FlashHeadProcessorCallbacks(on_audio_frame=on_audio_frame)
    )

    collector = threading.Thread(target=processor._frame_collector_worker)
    collector.start()

    assert second_frame_emitted.wait(timeout=1.0)
    collector.join(timeout=1.0)
    assert not collector.is_alive()
    assert audio_attempts == [None, None]
    assert processor._output_queue.empty()


class _MissingStreamStorage:
    @staticmethod
    def find_stream(_stream_id):
        return None


def test_streamer_removes_recycled_parent_before_creating_next_output():
    stream_id = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=7,
        stream_id=11,
        producer_name="CosyVoice",
    )
    streamer = object.__new__(ChatStreamer)
    streamer._input_stream_ids = {
        stream_id.key: InputStreamStats(stream_id=stream_id),
    }
    streamer._storage = _MissingStreamStorage()
    streamer._ended_input_retention = 3.0

    streamer._cleanup_input_streams()

    assert streamer._input_stream_ids == {}


def test_collector_emits_new_tts_frame_after_interrupt():
    emitted_speech_ids = []
    idle_frame_emitted = threading.Event()
    new_frame_emitted = threading.Event()
    processor = None

    def on_audio_frame(_audio, speech_id):
        emitted_speech_ids.append(speech_id)
        if speech_id is None:
            idle_frame_emitted.set()
        if speech_id == "new-speech":
            new_frame_emitted.set()
            processor._stop_event.set()

    processor = _make_collector_processor(
        FlashHeadProcessorCallbacks(on_audio_frame=on_audio_frame)
    )
    processor._interrupted = True
    processor._output_queue.put(_speech_frame("interrupted-speech"))

    collector = threading.Thread(target=processor._frame_collector_worker)
    collector.start()

    assert idle_frame_emitted.wait(timeout=1.0)
    processor._interrupted = False
    processor._output_queue.put(_speech_frame("new-speech"))

    assert new_frame_emitted.wait(timeout=1.0)
    collector.join(timeout=1.0)
    assert not collector.is_alive()
    assert "interrupted-speech" not in emitted_speech_ids
    assert "new-speech" in emitted_speech_ids
