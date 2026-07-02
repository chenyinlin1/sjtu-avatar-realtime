from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dashscope.audio.tts_v2 import VoiceEnrollmentService

from src.engine_utils.directory_info import DirectoryInfo


DEFAULT_MAX_AUDIO_BYTES = 10 * 1024 * 1024
DEFAULT_MIN_AUDIO_SECONDS = 5.0
DEFAULT_MAX_AUDIO_SECONDS = 60.0
DEFAULT_UPLOAD_RELATIVE_DIR = Path("resource") / "voice" / "cosyvoice" / "uploads"
ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/webm",
    "video/webm",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "application/octet-stream",
}


class VoiceCloneUploadError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class VoiceCloneAudioUploadResult:
    absolute_path: str
    relative_path: str
    filename: str
    duration_seconds: float
    sample_rate: int

    def to_response(self, voice_id: str, model_name: str) -> dict:
        return {
            "status": "ok",
            "voice_id": voice_id,
            "model_name": model_name,
            "audio_path": self.relative_path,
            "filename": self.filename,
            "duration_seconds": round(self.duration_seconds, 2),
            "sample_rate": self.sample_rate,
        }


def _default_project_root() -> Path:
    return Path(DirectoryInfo.get_project_dir())


def _default_upload_dir(project_root: Path) -> Path:
    return project_root / DEFAULT_UPLOAD_RELATIVE_DIR


def _safe_wav_filename() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"cosyvoice_clone_{timestamp}_{uuid.uuid4().hex[:12]}.wav"


def _source_suffix(original_filename: Optional[str], content_type: Optional[str]) -> str:
    suffix = Path(original_filename or "").suffix.lower()
    if suffix in {".wav", ".webm", ".mp3", ".m4a", ".mp4"}:
        return suffix
    normalized_content_type = (content_type or "").split(";")[0].strip().lower()
    if normalized_content_type in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return ".wav"
    if normalized_content_type in {"audio/webm", "video/webm"}:
        return ".webm"
    if normalized_content_type in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if normalized_content_type in {"audio/mp4", "audio/m4a"}:
        return ".m4a"
    return ".audio"


def _run_command(command: list[str], error_detail: str) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise VoiceCloneUploadError(500, "Audio converter is not installed on the server.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or error_detail).strip()
        raise VoiceCloneUploadError(400, detail or error_detail) from exc
    return result.stdout.strip()


def _probe_duration_seconds(audio_path: Path) -> float:
    output = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        "Unable to read the recorded audio duration.",
    )
    try:
        return float(output)
    except ValueError as exc:
        raise VoiceCloneUploadError(400, "Unable to read the recorded audio duration.") from exc


def save_voice_clone_audio_bytes(
    *,
    data: bytes,
    original_filename: Optional[str],
    content_type: Optional[str],
    upload_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    max_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
    min_seconds: float = DEFAULT_MIN_AUDIO_SECONDS,
    max_seconds: float = DEFAULT_MAX_AUDIO_SECONDS,
) -> VoiceCloneAudioUploadResult:
    if not data:
        raise VoiceCloneUploadError(400, "Uploaded audio is empty.")

    if len(data) > max_bytes:
        raise VoiceCloneUploadError(413, "Uploaded audio is too large.")

    normalized_content_type = (content_type or "").split(";")[0].strip().lower()
    if normalized_content_type and normalized_content_type not in ALLOWED_CONTENT_TYPES:
        raise VoiceCloneUploadError(400, "Uploaded file must be a readable audio recording.")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise VoiceCloneUploadError(500, "Audio converter is not installed on the server.")

    resolved_project_root = Path(project_root) if project_root is not None else _default_project_root()
    resolved_upload_dir = Path(upload_dir) if upload_dir is not None else _default_upload_dir(resolved_project_root)
    resolved_upload_dir.mkdir(parents=True, exist_ok=True)

    output_filename = _safe_wav_filename()
    output_path = resolved_upload_dir / output_filename
    source_path = resolved_upload_dir / f"{output_path.stem}_source{_source_suffix(original_filename, content_type)}"
    source_path.write_bytes(data)

    try:
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-sample_fmt",
                "s16",
                str(output_path),
            ],
            "Unable to convert the recording. Please record again in a quiet environment.",
        )
        duration_seconds = _probe_duration_seconds(output_path)
    finally:
        try:
            source_path.unlink()
        except FileNotFoundError:
            pass

    if duration_seconds < min_seconds:
        output_path.unlink(missing_ok=True)
        raise VoiceCloneUploadError(400, "Recording is too short. Please read the prompt for at least 5 seconds.")

    if duration_seconds > max_seconds:
        output_path.unlink(missing_ok=True)
        raise VoiceCloneUploadError(400, "Recording is too long. Please keep it within 60 seconds.")

    try:
        relative_path = output_path.relative_to(resolved_project_root)
    except ValueError:
        relative_path = output_path

    return VoiceCloneAudioUploadResult(
        absolute_path=str(output_path),
        relative_path=relative_path.as_posix(),
        filename=output_filename,
        duration_seconds=duration_seconds,
        sample_rate=24000,
    )


def find_voice_clone_audio_file(filename: str, *, project_root: Optional[Path] = None) -> Path:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".wav"):
        raise VoiceCloneUploadError(404, "Voice clone audio file was not found.")
    resolved_project_root = Path(project_root) if project_root is not None else _default_project_root()
    audio_path = _default_upload_dir(resolved_project_root) / safe_name
    if not audio_path.is_file():
        raise VoiceCloneUploadError(404, "Voice clone audio file was not found.")
    return audio_path


def create_cosyvoice_voice_clone(
    *,
    audio_url: str,
    target_model: str,
    prefix: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    voice_prefix = prefix or f"fh{uuid.uuid4().hex[:7]}"
    service = VoiceEnrollmentService(api_key=api_key or os.getenv("DASHSCOPE_API_KEY"))
    return service.create_voice(
        target_model=target_model,
        prefix=voice_prefix,
        url=audio_url,
    )


def is_voice_enrollment_download_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "inputdownloadfailed" in detail or "download audio failed" in detail
