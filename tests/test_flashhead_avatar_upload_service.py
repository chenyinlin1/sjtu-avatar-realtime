from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from service.frontend_service.avatar_image_upload import (
    AvatarImageUploadError,
    save_avatar_image_bytes,
)


def _image_bytes(image_format: str = "JPEG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=(20, 80, 160)).save(buffer, format=image_format)
    return buffer.getvalue()


def test_save_avatar_image_bytes_converts_uploaded_image_to_rgb_png(tmp_path):
    upload_dir = tmp_path / "resource" / "avatar" / "flashhead" / "uploads"

    result = save_avatar_image_bytes(
        data=_image_bytes("JPEG"),
        original_filename="person.jpg",
        content_type="image/jpeg",
        upload_dir=upload_dir,
        project_root=tmp_path,
        max_bytes=1024 * 1024,
    )

    saved_path = Path(result.absolute_path)
    assert saved_path.exists()
    assert result.filename.endswith(".png")
    assert result.relative_path.startswith("resource/avatar/flashhead/uploads/")

    with Image.open(saved_path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (8, 8)

    response = result.to_response()
    assert response["avatar_image_path"] == result.relative_path
    assert response["filename"] == result.filename


def test_save_avatar_image_bytes_rejects_non_image_upload(tmp_path):
    with pytest.raises(AvatarImageUploadError) as exc_info:
        save_avatar_image_bytes(
            data=b"not an image",
            original_filename="notes.txt",
            content_type="text/plain",
            upload_dir=tmp_path / "uploads",
            project_root=tmp_path,
        )

    assert exc_info.value.status_code == 400
    assert "image" in exc_info.value.detail.lower()


def test_save_avatar_image_bytes_rejects_oversized_upload(tmp_path):
    with pytest.raises(AvatarImageUploadError) as exc_info:
        save_avatar_image_bytes(
            data=b"x" * 11,
            original_filename="person.png",
            content_type="image/png",
            upload_dir=tmp_path / "uploads",
            project_root=tmp_path,
            max_bytes=10,
        )

    assert exc_info.value.status_code == 413
