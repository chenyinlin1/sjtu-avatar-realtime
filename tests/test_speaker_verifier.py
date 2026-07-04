import numpy as np

from handlers.voice_gate.speaker_verifier import DEFAULT_SPEAKER_MODEL, SpeakerVerifier


class _FakeFunASRModel:
    def __init__(self):
        self.inputs = []

    def generate(self, *, input):
        self.inputs.append(input)
        return [{"spk_embedding": [1.0, 2.0, 3.0]}]


class _FakeModelScopePipeline:
    def __init__(self):
        self.calls = []

    def __call__(self, audios, *, output_emb=False):
        self.calls.append((audios, output_emb))
        assert output_emb is True
        assert len(audios) == 1
        assert audios[0].dtype == np.int16
        return {"embs": np.array([[0.25, 0.5, 0.75]], dtype=np.float32)}


def test_speaker_verifier_uses_modelscope_for_eres2netv2():
    created = {}

    def pipeline_factory(**kwargs):
        created.update(kwargs)
        return _FakeModelScopePipeline()

    verifier = SpeakerVerifier(pipeline_factory=pipeline_factory)
    pcm = np.array([1, -2, 3], dtype="<i2").tobytes()

    assert verifier.backend == "modelscope"
    assert created == {"model": DEFAULT_SPEAKER_MODEL, "device": "cpu"}
    assert verifier.extract_embedding(pcm) == (0.25, 0.5, 0.75)


def test_speaker_verifier_keeps_funasr_factory_path_for_other_models():
    created = {}
    fake_model = _FakeFunASRModel()

    def model_factory(**kwargs):
        created.update(kwargs)
        return fake_model

    verifier = SpeakerVerifier(model_name="custom-speaker-model", model_factory=model_factory)
    pcm = b"abc"

    assert verifier.backend == "funasr"
    assert created == {
        "model": "custom-speaker-model",
        "device": "cpu",
        "disable_update": True,
    }
    assert verifier.extract_embedding(pcm) == (1.0, 2.0, 3.0)
    assert fake_model.inputs == [pcm]
