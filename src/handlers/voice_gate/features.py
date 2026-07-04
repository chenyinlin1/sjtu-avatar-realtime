from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class PcmStats:
    duration_ms: int
    rms: float
    dbfs: float


def pcm16_stats(pcm: bytes, *, sample_rate: int = 16_000) -> PcmStats:
    if len(pcm) % 2 != 0:
        raise ValueError("PCM16 frame length must be even")

    sample_count = len(pcm) // 2
    if sample_count == 0:
        return PcmStats(duration_ms=0, rms=0.0, dbfs=-120.0)

    samples = struct.unpack(f"<{sample_count}h", pcm)
    mean_square = sum(sample * sample for sample in samples) / sample_count
    rms = math.sqrt(mean_square) / 32768.0
    dbfs = 20 * math.log10(max(rms, 1e-6))
    duration_ms = round(sample_count / sample_rate * 1000)
    return PcmStats(duration_ms=duration_ms, rms=rms, dbfs=dbfs)


def audio_array_to_pcm16(audio: np.ndarray | None) -> bytes:
    if audio is None:
        return b""
    array = np.asarray(audio)
    if array.size == 0:
        return b""
    array = np.squeeze(array)
    if array.ndim > 1:
        array = array.reshape(-1)
    if array.dtype == np.int16:
        return array.astype("<i2", copy=False).tobytes()
    if np.issubdtype(array.dtype, np.floating):
        array = np.clip(array.astype(np.float32, copy=False), -1.0, 1.0)
        return (array * 32767.0).astype("<i2").tobytes()
    return np.clip(array, -32768, 32767).astype("<i2").tobytes()


def audio_array_to_float32(audio: np.ndarray | None) -> np.ndarray:
    if audio is None:
        return np.zeros(0, dtype=np.float32)
    array = np.asarray(audio)
    if array.size == 0:
        return np.zeros(0, dtype=np.float32)
    array = np.squeeze(array)
    if array.ndim > 1:
        array = array.reshape(-1)
    if array.dtype == np.float32:
        return array
    if np.issubdtype(array.dtype, np.floating):
        return array.astype(np.float32)
    return array.astype(np.float32) / 32768.0
