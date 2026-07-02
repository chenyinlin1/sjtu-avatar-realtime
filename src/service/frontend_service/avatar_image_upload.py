from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from src.engine_utils.directory_info import DirectoryInfo


DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 4096 * 4096
DEFAULT_UPLOAD_RELATIVE_DIR = Path("resource") / "avatar" / "flashhead" / "uploads"
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


class AvatarImageUploadError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class AvatarImageUploadResult:
    absolute_path: str
    relative_path: str
    filename: str
    width: int
    height: int

    def to_response(self) -> dict:
        return {
            "status": "ok",
            "avatar_image_path": self.relative_path,
            "filename": self.filename,
            "width": self.width,
            "height": self.height,
        }


def _default_project_root() -> Path:
    return Path(DirectoryInfo.get_project_dir())


def _default_upload_dir(project_root: Path) -> Path:
    return project_root / DEFAULT_UPLOAD_RELATIVE_DIR


def _safe_png_filename() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"flashhead_avatar_{timestamp}_{uuid.uuid4().hex[:12]}.png"


def save_avatar_image_bytes(
    *,
    data: bytes,
    original_filename: Optional[str],
    content_type: Optional[str],
    upload_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
) -> AvatarImageUploadResult:
    if not data:
        raise AvatarImageUploadError(400, "Uploaded image is empty.")

    if len(data) > max_bytes:
        raise AvatarImageUploadError(413, "Uploaded image is too large.")

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise AvatarImageUploadError(400, "Uploaded file must be a JPEG, PNG, or WebP image.")

    try:
        with Image.open(BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
            if width * height > max_pixels:
                raise AvatarImageUploadError(413, "Uploaded image dimensions are too large.")
            image = image.convert("RGB")
    except AvatarImageUploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        name = original_filename or "uploaded file"
        raise AvatarImageUploadError(400, f"{name} is not a readable image.") from exc

    resolved_project_root = Path(project_root) if project_root is not None else _default_project_root()
    resolved_upload_dir = Path(upload_dir) if upload_dir is not None else _default_upload_dir(resolved_project_root)
    resolved_upload_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_png_filename()
    absolute_path = resolved_upload_dir / filename
    image.save(absolute_path, format="PNG")

    try:
        relative_path = absolute_path.relative_to(resolved_project_root)
    except ValueError:
        relative_path = absolute_path

    return AvatarImageUploadResult(
        absolute_path=str(absolute_path),
        relative_path=relative_path.as_posix(),
        filename=filename,
        width=width,
        height=height,
    )
