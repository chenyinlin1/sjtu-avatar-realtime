from pathlib import Path


SEMANTIC_HANDLER = Path(
    "src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py"
)
DUPLEX_VAD_HANDLER = Path("src/handlers/vad/silerovad/duplex_vad_handler.py")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_semantic_detector_preempts_on_speech_start_before_asr():
    source = _source(SEMANTIC_HANDLER)

    assert "interrupt_on_speech_start" in source
    assert "speech_start_interrupt_streams" in source
    assert "_maybe_emit_speech_start_interrupt" in source
    assert "speech_start_barge_in" in source

    call_index = source.index("_maybe_emit_speech_start_interrupt(")
    accumulate_index = source.index("# Accumulate audio data")
    assert call_index < accumulate_index


def test_semantic_detector_preempts_pending_avatar_response_before_playback():
    source = _source(SEMANTIC_HANDLER)

    helper_index = source.index("_get_active_avatar_response_counts")
    speech_start_index = source.index("def _maybe_emit_speech_start_interrupt")
    assert helper_index < speech_start_index

    speech_start_block = source[
        speech_start_index:source.index("def _handle_partial_text", speech_start_index)
    ]
    assert "active_avatar_audio_count" in speech_start_block
    assert "active_avatar_text_count" in speech_start_block
    assert "active_avatar_response_count > 0" in speech_start_block
    assert "metadata_avatar_speaking" in speech_start_block


def test_duplex_vad_places_avatar_speaking_metadata_on_first_audio_bundle():
    source = _source(DUPLEX_VAD_HANDLER)

    metadata_index = source.index(
        'output.add_meta("avatar_was_speaking_at_stream_start"'
    )
    submit_index = source.index("context.submit_data(output_chat_data")
    assert metadata_index < submit_index
