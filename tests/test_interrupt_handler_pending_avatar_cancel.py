from types import SimpleNamespace

from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.core.stream_manager import ChatStream, StreamStorage
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import (
    ChatSignalSourceType,
    ChatSignalType,
)
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from chat_engine.data_models.chat_stream_config import ChatStreamConfig
from chat_engine.data_models.chat_stream_status import ChatStreamStatus
from handlers.avatar.flashhead.avatar_handler_flashhead import (
    FlashHeadContext,
    HandlerAvatarFlashHead,
)
from handlers.logic.interrupt.interrupt_handler import InterruptHandler


class FakeStreamManager:
    def __init__(self, active_identities):
        self.active_streams = [
            SimpleNamespace(identity=identity) for identity in active_identities
        ]
        self.cancelled = []

    def get_active_streams(self):
        return self.active_streams

    def cancel_stream_chain(self, stream_id):
        self.cancelled.append(stream_id)
        return [stream_id]


def test_interrupt_without_playback_cancels_pending_avatar_audio():
    avatar_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=100,
        stream_id=7,
        producer_name="CosyVoice",
    )
    manager = FakeStreamManager([avatar_audio])
    context = HandlerContext("test-session")
    context.stream_manager = manager

    InterruptHandler().on_signal(
        context,
        ChatSignal(
            type=ChatSignalType.INTERRUPT,
            source_type=ChatSignalSourceType.HANDLER,
            source_name="SemanticTurnDetector",
        ),
    )

    assert manager.cancelled == [avatar_audio]


def test_interrupt_cancels_all_active_avatar_response_streams():
    avatar_text = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_TEXT,
        builder_id=101,
        stream_id=1,
        producer_name="LLMOpenAICompatible",
    )
    tts_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=102,
        stream_id=2,
        name="bailian_tts",
        producer_name="CosyVoice",
    )
    playback = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=103,
        stream_id=3,
        name="playback:stream_102_2",
        producer_name="FlashHead",
    )
    flashhead_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=104,
        stream_id=4,
        producer_name="FlashHead",
    )
    human_text = ChatStreamIdentity(
        data_type=ChatDataType.HUMAN_TEXT,
        builder_id=105,
        stream_id=5,
        producer_name="SemanticTurnDetector",
    )
    manager = FakeStreamManager([
        playback,
        tts_audio,
        avatar_text,
        flashhead_audio,
        human_text,
    ])
    context = HandlerContext("test-session")
    context.stream_manager = manager

    InterruptHandler().on_signal(
        context,
        ChatSignal(
            type=ChatSignalType.INTERRUPT,
            source_type=ChatSignalSourceType.HANDLER,
            source_name="SemanticTurnDetector",
        ),
    )

    assert manager.cancelled == [
        avatar_text,
        tts_audio,
        playback,
        flashhead_audio,
    ]

class FakeSignalEmitter:
    def __init__(self):
        self.signals = []

    def emit(self, signal):
        self.signals.append(signal)


def test_cancel_chain_marks_ended_tts_ancestor_cancelled():
    emitter = FakeSignalEmitter()
    storage = StreamStorage()

    def add_stream(identity, *, sources=None, cancelable=True):
        stream = ChatStream(
            identity=identity,
            storage=storage,
            config=ChatStreamConfig(cancelable=cancelable),
            source_streams=sources or [],
            signal_emitter=emitter,
        )
        storage.add_stream(identity.key, stream)
        return stream

    avatar_text_id = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_TEXT,
        builder_id=201,
        stream_id=1,
        producer_name="LLMOpenAICompatible",
    )
    tts_audio_id = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=202,
        stream_id=2,
        name="bailian_tts",
        producer_name="CosyVoice",
    )
    playback_id = ChatStreamIdentity(
        data_type=ChatDataType.CLIENT_PLAYBACK,
        builder_id=203,
        stream_id=3,
        name="playback:stream_202_2",
        producer_name="FlashHead",
    )

    avatar_text = add_stream(avatar_text_id)
    tts_audio = add_stream(tts_audio_id, sources=[avatar_text_id])
    playback = add_stream(playback_id, sources=[tts_audio_id], cancelable=False)

    avatar_text.status = ChatStreamStatus.ENDED
    tts_audio.status = ChatStreamStatus.ENDED
    playback.status = ChatStreamStatus.STARTED

    playback.cancel_with_ancestors(storage)

    assert avatar_text.status == ChatStreamStatus.CANCELLED
    assert tts_audio.status == ChatStreamStatus.CANCELLED
    assert playback.status == ChatStreamStatus.CANCELLED


class FakeFlashHeadProcessor:
    def __init__(self):
        self.interrupt_count = 0
        self.added_audio = []

    def interrupt(self):
        self.interrupt_count += 1

    def add_audio(self, **kwargs):
        self.added_audio.append(kwargs)


def test_flashhead_drops_audio_from_interrupted_tts_stream():
    processor = FakeFlashHeadProcessor()
    context = FlashHeadContext("test-session", processor)
    tts_audio_id = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=301,
        stream_id=9,
        name="bailian_tts",
        producer_name="CosyVoice",
    )
    context._current_tts_stream_key = tts_audio_id.stream_key_str

    context.interrupt()

    assert processor.interrupt_count == 1
    assert tts_audio_id.stream_key_str in context._interrupted_tts_stream_keys

    handler = object.__new__(HandlerAvatarFlashHead)
    handler.handle(
        context,
        ChatData(type=ChatDataType.AVATAR_AUDIO, stream_id=tts_audio_id),
        {},
    )

    assert processor.added_audio == []

