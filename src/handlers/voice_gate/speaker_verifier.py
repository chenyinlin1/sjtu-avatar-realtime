from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np


DEFAULT_SPEAKER_MODEL = "iic/speech_eres2netv2_sv_zh-cn_16k-common"
ERES2NETV2_MODEL_MARKER = "speech_eres2netv2_sv_zh-cn_16k-common"


class SpeakerVerifier:
    def __init__(
        self,
        model_name: str = DEFAULT_SPEAKER_MODEL,
        *,
        device: str = "cpu",
        model_factory: Callable[..., Any] | None = None,
        pipeline_factory: Callable[..., Any] | None = None,
    ):
        self.model_name = model_name
        self.device = device
        self.backend = (
            "modelscope"
            if _is_eres2netv2_model(model_name) and model_factory is None
            else "funasr"
        )

        if self.backend == "modelscope":
            if pipeline_factory is None:
                from modelscope.pipelines import pipeline
                from modelscope.utils.constant import Tasks

                pipeline_factory = lambda **kwargs: pipeline(
                    task=Tasks.speaker_verification, **kwargs
                )
            self.model = pipeline_factory(model=model_name, device=device)
        else:
            if model_factory is None:
                from funasr import AutoModel

                model_factory = AutoModel
            self.model = model_factory(model=model_name, device=device, disable_update=True)

    def extract_embedding(self, pcm: bytes) -> tuple[float, ...]:
        if self.backend == "modelscope":
            samples = _pcm16_to_int16_array(pcm)
            if samples.size == 0:
                return ()
            result = self.model([samples], output_emb=True)
        else:
            result = self.model.generate(input=pcm)
        return _extract_embedding(result)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    left_part = left[:length]
    right_part = right[:length]
    left_norm = math.sqrt(sum(value * value for value in left_part))
    right_norm = math.sqrt(sum(value * value for value in right_part))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(left_part[index] * right_part[index] for index in range(length)) / (
        left_norm * right_norm
    )


def _is_eres2netv2_model(model_name: str) -> bool:
    return ERES2NETV2_MODEL_MARKER in model_name


def _pcm16_to_int16_array(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.zeros(0, dtype=np.int16)
    return np.frombuffer(pcm, dtype="<i2")


def _extract_embedding(result: Any) -> tuple[float, ...]:
    if not result:
        return ()
    if isinstance(result, list):
        first = result[0] if result else {}
    else:
        first = result
    if not isinstance(first, dict):
        return ()

    for key in ("spk_embedding", "embedding", "emb", "vector", "embs"):
        value = first.get(key)
        if value is not None:
            return tuple(float(item) for item in _flatten_embedding(value))
    return ()


def _flatten_embedding(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    if hasattr(value, "tolist"):
        value = value.tolist()

    flat: list[float] = []

    def visit(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        flat.append(float(item))

    visit(value)
    return flat
