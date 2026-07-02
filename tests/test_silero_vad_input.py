import numpy as np

from handlers.vad.silerovad.vad_handler_silero import (
    HandlerAudioVAD,
    HumanAudioVADContext,
    SileroVADConfigModel,
    SpeakingStatus,
    _apply_energy_speech_fallback,
    _prepare_vad_model_clip,
)


def test_prepare_vad_model_clip_preserves_normalized_audio_for_onnx_model():
    clip = np.array([-0.5, 0.0, 0.5], dtype=np.float32)

    prepared = _prepare_vad_model_clip(clip)

    assert prepared.dtype == np.float32
    np.testing.assert_allclose(prepared, clip)


def test_prepare_vad_model_clip_normalizes_int16_scale_audio():
    clip = np.array([-32768.0, 0.0, 32767.0], dtype=np.float32)

    prepared = _prepare_vad_model_clip(clip)

    np.testing.assert_allclose(
        prepared,
        np.array([-1.0, 0.0, 32767.0 / 32768.0], dtype=np.float32),
    )


def test_silero_onnx_inference_prepends_model_context():
    class FakeModel:
        def __init__(self):
            self.calls = []

        def run(self, _output_names, inputs):
            self.calls.append(inputs)
            return np.array([[0.0]], dtype=np.float32), inputs["state"]

    handler = HandlerAudioVAD()
    handler.model = FakeModel()
    context = HumanAudioVADContext("test-session")
    context.reset_model()
    clip = np.linspace(-0.5, 0.5, 512, dtype=np.float32)

    handler._inference(context, clip, sr=16000)

    model_input = handler.model.calls[0]["input"]
    assert model_input.shape == (1, 576)
    np.testing.assert_allclose(model_input[0, :64], np.zeros(64, dtype=np.float32))
    np.testing.assert_allclose(model_input[0, 64:], clip)
    np.testing.assert_allclose(context.model_context, clip[-64:])
    assert handler.model.calls[0]["sr"].shape == ()


def test_energy_speech_fallback_promotes_loud_browser_audio():
    config = SileroVADConfigModel(
        speaking_threshold=0.25,
        energy_speech_threshold=-55.0,
    )

    prob, used = _apply_energy_speech_fallback(
        speech_prob=0.001,
        db=-45.0,
        config=config,
    )

    assert used is True
    assert prob > config.speaking_threshold


def test_energy_speech_fallback_ignores_quiet_background_audio():
    config = SileroVADConfigModel(
        speaking_threshold=0.25,
        energy_speech_threshold=-55.0,
    )

    prob, used = _apply_energy_speech_fallback(
        speech_prob=0.001,
        db=-80.0,
        config=config,
    )

    assert used is False
    assert prob == 0.001


def test_energy_speech_fallback_is_disabled_by_default():
    config = SileroVADConfigModel(speaking_threshold=0.25)

    prob, used = _apply_energy_speech_fallback(
        speech_prob=0.001,
        db=-10.0,
        config=config,
    )

    assert used is False
    assert prob == 0.001


def test_start_delay_one_clip_starts_immediately_from_end_state():
    context = HumanAudioVADContext("test-session")
    context.config = SileroVADConfigModel(
        speaking_threshold=0.25,
        start_delay=512,
        buffer_look_back=0,
        speech_padding=0,
    )
    clip = np.ones(512, dtype=np.float32) * 0.1

    output_audio, extra_args = context.update_status(1.0, clip, timestamp=123)

    assert context.speaking_status == SpeakingStatus.START
    assert extra_args["human_speech_start"] is True
    assert extra_args["speech_length_at_start"] == 512
    assert extra_args["head_sample_id"] == 123
    np.testing.assert_allclose(output_audio, clip)
