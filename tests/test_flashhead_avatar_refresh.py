from pathlib import Path

from PIL import Image

from handlers.avatar.flashhead.avatar_handler_flashhead import HandlerAvatarFlashHead
from handlers.avatar.flashhead.flashhead_config import FlashHeadConfig


def test_update_condition_image_rebuilds_flashhead_base_data(monkeypatch, tmp_path):
    image_path = tmp_path / "person.png"
    Image.new("RGB", (8, 8), color=(100, 80, 60)).save(image_path)

    algo_path = tmp_path / "SoulX-FlashHead"
    algo_path.mkdir()

    handler = HandlerAvatarFlashHead()
    handler.pipeline = object()
    handler._handler_config = FlashHeadConfig(base_seed=123, use_face_crop=False)
    handler._flashhead_algo_path = str(algo_path)

    calls = []

    def fake_get_base_data(*, pipeline, cond_image_path_or_dir, base_seed, use_face_crop):
        calls.append(
            {
                "pipeline": pipeline,
                "cond_image_path_or_dir": cond_image_path_or_dir,
                "base_seed": base_seed,
                "use_face_crop": use_face_crop,
            }
        )

    monkeypatch.setattr(handler, "_import_get_base_data", lambda: fake_get_base_data)

    handler.update_condition_image(str(image_path))

    assert calls == [
        {
            "pipeline": handler.pipeline,
            "cond_image_path_or_dir": str(image_path),
            "base_seed": 123,
            "use_face_crop": False,
        }
    ]
    assert handler._condition_image_path == str(image_path)


def test_update_condition_image_requires_existing_file(tmp_path):
    handler = HandlerAvatarFlashHead()
    handler.pipeline = object()
    handler._handler_config = FlashHeadConfig()

    missing_path = tmp_path / "missing.png"

    try:
        handler.update_condition_image(str(missing_path))
    except FileNotFoundError as exc:
        assert str(missing_path) in str(exc)
    else:
        raise AssertionError("update_condition_image should reject missing image files")
