from __future__ import annotations

import hashlib
import re
import shutil
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .repository import default_storage_root


MAX_VOICE_BYTES = 20 * 1024 * 1024
MAX_FACE_BYTES = 10 * 1024 * 1024


class MediaStorageError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class StoredMedia:
    absolute_path: Path
    stored_path: str
    duration_ms: Optional[int] = None


class PersonaMediaStorage:
    def __init__(self, storage_root: Optional[Path] = None):
        self.storage_root = Path(storage_root).expanduser().resolve() if storage_root else default_storage_root()
        self.media_root = self.storage_root / "media"

    def save_voice_bytes(self, persona_id: str, filename: str, content: bytes) -> StoredMedia:
        if not content:
            raise MediaStorageError("INVALID_PARAM", "voice sample is empty", 400)
        if len(content) > MAX_VOICE_BYTES:
            raise MediaStorageError("PAYLOAD_TOO_LARGE", "voice sample is too large", 413)
        if self._suffix(filename) != ".wav":
            raise MediaStorageError("UNSUPPORTED_MEDIA", "voice sample must be a wav file", 415)
        path = self._persona_dir(persona_id) / "voice_sample.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        duration_ms = self._read_wav_duration_ms(path)
        return StoredMedia(path.resolve(), self._absolute_path(path), duration_ms)

    def save_face_bytes(self, persona_id: str, filename: str, content: bytes) -> StoredMedia:
        if not content:
            raise MediaStorageError("INVALID_PARAM", "face image is empty", 400)
        if len(content) > MAX_FACE_BYTES:
            raise MediaStorageError("PAYLOAD_TOO_LARGE", "face image is too large", 413)
        suffix = self._suffix(filename)
        if suffix not in {".jpg", ".jpeg", ".png"}:
            raise MediaStorageError("UNSUPPORTED_MEDIA", "face image must be jpg or png", 415)
        target_suffix = ".jpg" if suffix == ".jpeg" else suffix
        path = self._persona_dir(persona_id) / f"face{target_suffix}"
        self.delete_face(persona_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredMedia(path.resolve(), self._absolute_path(path))

    def download(self, url: str, max_bytes: int) -> Tuple[bytes, str]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise MediaStorageError("INVALID_PARAM", "url must use http or https", 400)
        filename = Path(parsed.path).name or "download"
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                content = response.read(max_bytes + 1)
        except Exception as exc:
            raise MediaStorageError("INVALID_PARAM", f"failed to download media: {exc}", 400) from exc
        if len(content) > max_bytes:
            raise MediaStorageError("PAYLOAD_TOO_LARGE", "downloaded media is too large", 413)
        return content, filename

    def delete_voice(self, persona_id: str) -> None:
        voice_path = self._persona_dir(persona_id) / "voice_sample.wav"
        if voice_path.exists():
            voice_path.unlink()

    def delete_face(self, persona_id: str) -> None:
        persona_dir = self._persona_dir(persona_id)
        for suffix in (".jpg", ".png"):
            image_path = persona_dir / f"face{suffix}"
            if image_path.exists():
                image_path.unlink()

    def delete_persona_media(self, persona_id: str) -> None:
        persona_dir = self._persona_dir(persona_id)
        if persona_dir.exists():
            shutil.rmtree(persona_dir)

    def _persona_dir(self, persona_id: str) -> Path:
        if "/" in persona_id or "\\" in persona_id or ".." in persona_id:
            raise MediaStorageError("INVALID_PARAM", "invalid persona_id", 400)
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", persona_id).strip("._-")[:48] or "persona"
        digest = hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:12]
        return self.media_root / f"{cleaned}_{digest}"

    @staticmethod
    def _absolute_path(path: Path) -> str:
        return path.resolve().as_posix()

    @staticmethod
    def _suffix(filename: str) -> str:
        return Path(filename or "").suffix.lower()

    @staticmethod
    def _read_wav_duration_ms(path: Path) -> int:
        try:
            with wave.open(str(path), "rb") as wav_file:
                frames = wav_file.getnframes()
                sample_rate = wav_file.getframerate()
                if sample_rate <= 0:
                    raise ValueError("invalid sample rate")
                return int(frames * 1000 / sample_rate)
        except Exception as exc:
            raise MediaStorageError("UNSUPPORTED_MEDIA", f"invalid wav file: {exc}", 415) from exc
