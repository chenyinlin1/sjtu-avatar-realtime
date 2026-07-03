from types import SimpleNamespace

from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_stream import ChatStreamIdentity
from chat_engine.data_models.runtime_data.data_bundle import (
    DataBundle,
    DataBundleDefinition,
    DataBundleEntry,
)
from handlers.llm.semantic_turn_detector.semantic_turn_detector_handler import (
    SemanticTurnDetectorContext,
    SemanticTurnDetectorHandler,
)


def _text_bundle(text: str, **metadata):
    definition = DataBundleDefinition()
    definition.add_entry(DataBundleEntry.create_text_entry("text"))
    bundle = DataBundle(definition)
    bundle.set_main_data(text)
    for name, value in metadata.items():
        bundle.add_meta(name, value)
    return bundle


class RecordingSemanticTurnDetector(SemanticTurnDetectorHandler):
    def __init__(self):
        super().__init__()
        self.submitted_texts = []

    def _detect_interrupt_llm(self, context, user_text, avatar_text):
        return "不打断"

    def _judge_interrupt_intent(self, context, text, avatar_text):
        return "pure_interrupt"

    def _submit_human_text(self, context, text, inputs, output_definitions):
        self.submitted_texts.append(text)


class RecordingDetectedInterruptSemanticTurnDetector(RecordingSemanticTurnDetector):
    def _detect_interrupt_llm(self, context, user_text, avatar_text):
        return "打断"


def test_speech_start_barge_in_marks_human_audio_stream_as_preempted():
    handler = RecordingSemanticTurnDetector()
    context = SemanticTurnDetectorContext("test-session")

    human_audio = ChatStreamIdentity(
        data_type=ChatDataType.HUMAN_DUPLEX_AUDIO,
        builder_id=10,
        stream_id=3,
        producer_name="SileroVad",
    )
    avatar_audio = ChatStreamIdentity(
        data_type=ChatDataType.AVATAR_AUDIO,
        builder_id=20,
        stream_id=4,
        producer_name="CosyVoice",
    )
    stream = SimpleNamespace(
        identity=human_audio,
        inherited={},
        update_inheritable_metadata=lambda metadata, inherit=True: stream.inherited.update(metadata),
    )
    context.stream_manager = SimpleNamespace(
        get_active_streams=lambda: [SimpleNamespace(identity=avatar_audio)],
        find_stream=lambda stream_id: stream if stream_id == human_audio else None,
    )

    inputs = ChatData(
        type=ChatDataType.HUMAN_DUPLEX_AUDIO,
        stream_id=human_audio,
        data=_text_bundle("audio-placeholder"),
    )

    handler._maybe_emit_speech_start_interrupt(
        context,
        inputs,
        human_audio.stream_key_str,
    )

    assert stream.inherited["speech_start_barge_in_triggered"] is True


def test_no_interrupt_result_submits_text_after_speech_start_barge_in():
    handler = RecordingSemanticTurnDetector()
    context = SemanticTurnDetectorContext("test-session")
    context.llm_client = object()

    inputs = ChatData(
        type=ChatDataType.HUMAN_DUPLEX_TEXT,
        stream_id=ChatStreamIdentity(
            data_type=ChatDataType.HUMAN_DUPLEX_TEXT,
            builder_id=30,
            stream_id=5,
            producer_name="SenseVoice",
        ),
        data=_text_bundle(
            "哎我打断之后你好像就不会说话了",
            avatar_was_speaking_at_stream_start=True,
            speech_start_barge_in_triggered=True,
        ),
        is_last_data=True,
    )

    handler._handle_duplex_text(
        context,
        inputs,
        {ChatDataType.HUMAN_TEXT: SimpleNamespace()},
    )

    assert handler.submitted_texts == ["哎我打断之后你好像就不会说话了"]


def test_pure_interrupt_classification_still_submits_barge_in_request_text():
    handler = RecordingDetectedInterruptSemanticTurnDetector()
    context = SemanticTurnDetectorContext("test-session")
    context.llm_client = object()

    inputs = ChatData(
        type=ChatDataType.HUMAN_DUPLEX_TEXT,
        stream_id=ChatStreamIdentity(
            data_type=ChatDataType.HUMAN_DUPLEX_TEXT,
            builder_id=31,
            stream_id=6,
            producer_name="SenseVoice",
        ),
        data=_text_bundle(
            "好你不要说话了换个别的话说",
            avatar_was_speaking_at_stream_start=True,
            speech_start_barge_in_triggered=True,
        ),
        is_last_data=True,
    )

    handler._handle_duplex_text(
        context,
        inputs,
        {ChatDataType.HUMAN_TEXT: SimpleNamespace()},
    )

    assert handler.submitted_texts == ["好你不要说话了换个别的话说"]


def test_short_substantive_barge_in_text_is_submitted():
    handler = RecordingDetectedInterruptSemanticTurnDetector()
    context = SemanticTurnDetectorContext("test-session")
    context.llm_client = object()

    inputs = ChatData(
        type=ChatDataType.HUMAN_DUPLEX_TEXT,
        stream_id=ChatStreamIdentity(
            data_type=ChatDataType.HUMAN_DUPLEX_TEXT,
            builder_id=32,
            stream_id=7,
            producer_name="SenseVoice",
        ),
        data=_text_bundle(
            "对",
            avatar_was_speaking_at_stream_start=True,
            speech_start_barge_in_triggered=True,
        ),
        is_last_data=True,
    )

    handler._handle_duplex_text(
        context,
        inputs,
        {ChatDataType.HUMAN_TEXT: SimpleNamespace()},
    )

    assert handler.submitted_texts == ["对"]


def test_pure_stop_barge_in_text_is_not_submitted():
    handler = RecordingDetectedInterruptSemanticTurnDetector()
    context = SemanticTurnDetectorContext("test-session")
    context.llm_client = object()

    inputs = ChatData(
        type=ChatDataType.HUMAN_DUPLEX_TEXT,
        stream_id=ChatStreamIdentity(
            data_type=ChatDataType.HUMAN_DUPLEX_TEXT,
            builder_id=33,
            stream_id=8,
            producer_name="SenseVoice",
        ),
        data=_text_bundle(
            "好你不要说话了",
            avatar_was_speaking_at_stream_start=True,
            speech_start_barge_in_triggered=True,
        ),
        is_last_data=True,
    )

    handler._handle_duplex_text(
        context,
        inputs,
        {ChatDataType.HUMAN_TEXT: SimpleNamespace()},
    )

    assert handler.submitted_texts == []
