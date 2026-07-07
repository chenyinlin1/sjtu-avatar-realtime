from types import SimpleNamespace

import numpy as np

from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import ChatSignalType
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundle,
    DataBundleDefinition,
    DataBundleEntry,
)
from handlers.asr.bailian_asr import asr_handler_bailian as asr_module


class FakeRecognition:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.frames = []
        self.started = False
        self.stopped = False
        FakeRecognition.instances.append(self)

    def start(self):
        self.started = True

    def send_audio_frame(self, frame):
        assert self.started
        assert not self.stopped
        self.frames.append(frame)

    def stop(self):
        if self.stopped:
            raise RuntimeError("already stopped")
        self.stopped = True
        if not self.callback.sentences:
            self.callback.sentences.append("你好")
        self.callback.completed.set()

    def get_last_request_id(self):
        return "fake-request"

    def get_first_package_delay(self):
        return 1

    def get_last_package_delay(self):
        return 2


class FakeStreamer:
    def __init__(self):
        self.data_definition = DataBundleDefinition()
        self.data_definition.add_entry(DataBundleEntry.create_text_entry("human_text"))
        self.current_stream = None
        self.new_stream_sources = []
        self.outputs = []

    def new_stream(self, sources):
        self.new_stream_sources.append(sources)
        self.current_stream = SimpleNamespace(
            identity=ChatStreamIdentity(
                data_type=ChatDataType.HUMAN_TEXT,
                builder_id=99,
                stream_id=len(self.new_stream_sources),
            )
        )

    def stream_data(self, output, finish_stream=False):
        self.outputs.append((output, finish_stream))


class FakeSubmitter:
    def __init__(self):
        self.streamer = FakeStreamer()

    def get_streamer(self, data_type):
        assert data_type == ChatDataType.HUMAN_TEXT
        return self.streamer


def _audio_bundle(samples):
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_audio_entry("audio", 1, 16000))
    bundle = DataBundle(definition)
    bundle.set_main_data(np.asarray(samples, dtype=np.float32).reshape(1, -1))
    return bundle


def _audio_chat(stream_id, samples, is_last_data=False):
    return ChatData(
        type=ChatDataType.HUMAN_AUDIO,
        stream_id=stream_id,
        data=_audio_bundle(samples),
        is_last_data=is_last_data,
    )


def _context_and_handler(monkeypatch, tmp_path):
    FakeRecognition.instances.clear()
    monkeypatch.setattr(asr_module, "Recognition", FakeRecognition)
    monkeypatch.setattr(asr_module, "audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(asr_module.DirectoryInfo, "get_project_dir", lambda: str(tmp_path))

    context = asr_module.BailianASRContext("test-session")
    if context.audio_dump_file is not None:
        context.audio_dump_file.close()
        context.audio_dump_file = None
    context.data_submitter = FakeSubmitter()
    return context, asr_module.HandlerASR(), context.data_submitter.streamer


def test_bailian_asr_starts_and_sends_audio_before_speech_end(monkeypatch, tmp_path):
    context, handler, streamer = _context_and_handler(monkeypatch, tmp_path)
    handler.final_silence_padding_ms = 250
    input_stream = ChatStreamIdentity(
        data_type=ChatDataType.HUMAN_AUDIO,
        builder_id=10,
        stream_id=3,
        producer_name="SileroVad",
    )

    context.handle_audio_stream(
        _audio_chat(input_stream, np.ones(3200, dtype=np.float32), is_last_data=False),
        handler,
    )

    recognition = FakeRecognition.instances[0]
    assert recognition.started is True
    assert recognition.stopped is False
    assert len(recognition.frames) == 2
    assert all(len(frame) == 3200 for frame in recognition.frames)
    assert streamer.outputs == []
    assert input_stream.key in context.api_links

    context.handle_audio_stream(
        _audio_chat(input_stream, np.ones(800, dtype=np.float32), is_last_data=True),
        handler,
    )

    assert recognition.stopped is True
    # Two streaming frames, one final remainder, then configurable 250ms silence padding.
    assert [len(frame) for frame in recognition.frames] == [3200, 3200, 1600, 8000]
    assert set(recognition.frames[-1]) == {0}
    assert input_stream.key not in context.api_links
    assert len(streamer.outputs) == 1
    output, finish_stream = streamer.outputs[0]
    assert output.get_main_data() == "你好"
    assert output.get_meta("human_text_end") is True
    assert finish_stream is True


def test_bailian_asr_cancel_stops_active_recognition(monkeypatch, tmp_path):
    context, handler, streamer = _context_and_handler(monkeypatch, tmp_path)
    input_stream = ChatStreamIdentity(
        data_type=ChatDataType.HUMAN_AUDIO,
        builder_id=11,
        stream_id=4,
        producer_name="SileroVad",
    )

    context.handle_audio_stream(
        _audio_chat(input_stream, np.ones(1600, dtype=np.float32), is_last_data=False),
        handler,
    )

    recognition = FakeRecognition.instances[0]
    handler.on_signal(
        context,
        ChatSignal(type=ChatSignalType.STREAM_CANCEL, related_stream=input_stream),
    )

    assert recognition.stopped is True
    assert input_stream.key not in context.api_links
    assert streamer.outputs == []
