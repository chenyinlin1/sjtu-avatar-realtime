from types import SimpleNamespace

from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal
from chat_engine.data_models.chat_signal_type import (
    ChatSignalSourceType,
    ChatSignalType,
)
from chat_engine.data_models.chat_stream import ChatStreamIdentity
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
