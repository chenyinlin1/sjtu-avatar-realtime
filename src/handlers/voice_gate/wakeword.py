from __future__ import annotations

import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


DEFAULT_KWS_MODEL = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile"
DEFAULT_KWS_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    f"{DEFAULT_KWS_MODEL}.tar.bz2"
)
PROJECT_KWS_KEYWORDS = "x iǎo b àn x iǎo b àn @小伴小伴\n"
DEFAULT_PACKAGED_KWS_ARCHIVE = Path(__file__).with_name(f"{DEFAULT_KWS_MODEL}.tar.bz2")


class WakeDetector(Protocol):
    def accept_pcm16(self, pcm: bytes) -> str | None:
        ...


class NullWakeDetector:
    def accept_pcm16(self, pcm: bytes) -> str | None:
        return None


class LazyWakeDetector:
    def __init__(
        self,
        config_factory: Callable[[], "KeywordSpotterConfig"],
        *,
        detector_factory: Callable[["KeywordSpotterConfig"], WakeDetector] | None = None,
        prepare_model: Callable[[], None] | None = None,
    ) -> None:
        self._config_factory = config_factory
        self._detector_factory = detector_factory or SherpaKeywordSpotter
        self._prepare_model = prepare_model
        self._prepare_attempted = False
        self._impl: WakeDetector | None = None
        self.available = False

    def accept_pcm16(self, pcm: bytes) -> str | None:
        impl = self._get_impl()
        if impl is None:
            return None
        return impl.accept_pcm16(pcm)

    def reset(self) -> None:
        if self._impl is not None and hasattr(self._impl, "reset"):
            self._impl.reset()

    def _get_impl(self) -> WakeDetector | None:
        if self._impl is not None:
            return self._impl

        if self._prepare_model is not None and not self._prepare_attempted:
            self._prepare_attempted = True
            self._prepare_model()

        config = self._config_factory()
        if not config.exists():
            self.available = False
            return None

        self._impl = self._detector_factory(config)
        self.available = True
        return self._impl


@dataclass(frozen=True, slots=True)
class KeywordSpotterConfig:
    tokens: Path
    encoder: Path
    decoder: Path
    joiner: Path
    keywords_file: Path
    sample_rate: int = 16_000
    num_threads: int = 2
    keywords_score: float = 1.5
    keywords_threshold: float = 0.35
    provider: str = "cpu"

    @classmethod
    def from_model_dir(cls, model_dir: Path, *, provider: str = "cpu") -> "KeywordSpotterConfig":
        return cls(
            tokens=model_dir / "tokens.txt",
            encoder=_first_existing(
                model_dir,
                [
                    "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
                    "encoder.onnx",
                ],
            ),
            decoder=_first_existing(
                model_dir,
                [
                    "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
                    "decoder.onnx",
                ],
            ),
            joiner=_first_existing(
                model_dir,
                [
                    "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
                    "joiner.onnx",
                ],
            ),
            keywords_file=_first_existing(
                model_dir,
                [
                    "xiaoban_keywords.txt",
                    "keywords.txt",
                    "test_wavs/test_keywords.txt",
                ],
            ),
            provider=provider,
        )

    def exists(self) -> bool:
        return all(
            path.is_file()
            for path in (self.tokens, self.encoder, self.decoder, self.joiner, self.keywords_file)
        )


class SherpaKeywordSpotter:
    def __init__(
        self,
        config: KeywordSpotterConfig,
        *,
        spotter_factory: Callable[..., object] | None = None,
    ):
        if not config.exists():
            raise FileNotFoundError("sherpa-onnx KWS model files are incomplete")

        if spotter_factory is None:
            import sherpa_onnx

            spotter_factory = sherpa_onnx.KeywordSpotter

        self.config = config
        self.spotter = spotter_factory(
            tokens=str(config.tokens),
            encoder=str(config.encoder),
            decoder=str(config.decoder),
            joiner=str(config.joiner),
            keywords_file=str(config.keywords_file),
            num_threads=config.num_threads,
            sample_rate=config.sample_rate,
            keywords_score=config.keywords_score,
            keywords_threshold=config.keywords_threshold,
            provider=config.provider,
        )
        self.stream = self.spotter.create_stream()

    def accept_pcm16(self, pcm: bytes) -> str | None:
        samples = _pcm16_to_float32(pcm)
        self.stream.accept_waveform(self.config.sample_rate, samples)

        keyword = None
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result = self.spotter.get_result(self.stream)
            if result:
                keyword = result
                self.spotter.reset_stream(self.stream)
                break

        return keyword

    def reset(self) -> None:
        self.spotter.reset_stream(self.stream)


def _first_existing(model_dir: Path, candidates: list[str]) -> Path:
    for candidate in candidates:
        path = model_dir / candidate
        if path.exists():
            return path
    return model_dir / candidates[0]


def ensure_keyword_spotter_model(
    model_dir: Path,
    *,
    model_url: str = DEFAULT_KWS_MODEL_URL,
    local_archive: Path | None = None,
    downloader: Callable[[str, Path], None] | None = None,
) -> KeywordSpotterConfig:
    model_dir = Path(model_dir)
    config = KeywordSpotterConfig.from_model_dir(model_dir)
    if config.exists():
        _ensure_project_wake_keyword_file(model_dir)
        return KeywordSpotterConfig.from_model_dir(model_dir)

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    if local_archive is not None:
        packaged_archive = Path(local_archive)
    elif model_dir.name == DEFAULT_KWS_MODEL:
        packaged_archive = DEFAULT_PACKAGED_KWS_ARCHIVE
    else:
        packaged_archive = Path()
    if packaged_archive.is_file():
        archive_path = packaged_archive
    else:
        archive_path = model_dir.parent / f"{model_dir.name}.tar.bz2"
        download = downloader or _download_file
        download(model_url, archive_path)

    with tarfile.open(archive_path, mode="r:bz2") as archive:
        _safe_extract(archive, model_dir.parent)

    _ensure_project_wake_keyword_file(model_dir)
    config = KeywordSpotterConfig.from_model_dir(model_dir)
    if not config.exists():
        raise RuntimeError(f"KWS model files are incomplete after extraction: {model_dir}")
    return config


def _download_file(url: str, target: Path) -> None:
    urllib.request.urlretrieve(url, target)


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if not target.is_relative_to(destination):
            raise RuntimeError(f"Unsafe archive member path: {member.name}")
    archive.extractall(destination, filter="data")


def _ensure_project_wake_keyword_file(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / "xiaoban_keywords.txt"
    if target.exists() and target.read_text(encoding="utf-8") == PROJECT_KWS_KEYWORDS:
        return
    target.write_text(PROJECT_KWS_KEYWORDS, encoding="utf-8")


def _pcm16_to_float32(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm, dtype="<i2")
    return samples.astype(np.float32) / 32768.0
