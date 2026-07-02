import re
from pathlib import Path


COMPONENT = Path(
    "src/service/frontend_service/frontend/src/renderer/src/components/AvatarCloneControl.vue"
)


def _component_source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"async function {function_name}\([^)]*\): Promise<void> \{{(.*?)\n\}}",
        source,
        flags=re.S,
    )
    assert match is not None, f"{function_name} should exist"
    return match.group(1)


def test_avatar_clone_opens_dialog_with_camera_guide_and_confirmation():
    source = _component_source()

    assert "clone-dialog-overlay" in source
    assert "portrait-guide" in source
    assert "guide-face" in source
    assert "guide-shoulders" in source
    assert "使用这张照片" in source
    assert "重新拍摄" in source
    assert "重新选择" in source


def test_camera_capture_previews_photo_before_uploading():
    source = _component_source()
    capture_body = _function_body(source, "captureFromCamera")

    assert "uploadAvatarFile" not in capture_body
    assert "replacePreviewUrl" in capture_body
    assert "selectedFile" in capture_body


def test_upload_only_happens_from_confirmation_action():
    source = _component_source()

    assert "async function confirmUpload" in source
    assert "await uploadAvatarFile(selectedFile.value)" in source
