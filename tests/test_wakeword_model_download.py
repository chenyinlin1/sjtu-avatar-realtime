from pathlib import Path
import tarfile

from handlers.voice_gate.wakeword import (
    KeywordSpotterConfig,
    ensure_keyword_spotter_model,
)


def test_ensure_keyword_spotter_model_downloads_extracts_and_writes_xiaoban_keywords(tmp_path):
    source_root = tmp_path / "source"
    model_name = "sherpa-onnx-kws-test"
    archive = tmp_path / "source.tar.bz2"
    model_source = source_root / model_name
    model_source.mkdir(parents=True)
    for name in (
        "tokens.txt",
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    ):
        (model_source / name).write_text("stub", encoding="utf-8")
    (model_source / "keywords.txt").write_text("old keyword\n", encoding="utf-8")
    with tarfile.open(archive, mode="w:bz2") as tar:
        tar.add(model_source, arcname=model_name)

    downloads = []

    def fake_downloader(url: str, target: Path) -> None:
        downloads.append((url, target))
        target.write_bytes(archive.read_bytes())

    model_dir = tmp_path / "models" / model_name

    ensure_keyword_spotter_model(
        model_dir,
        model_url="https://example.test/kws.tar.bz2",
        downloader=fake_downloader,
    )

    config = KeywordSpotterConfig.from_model_dir(model_dir)
    assert config.exists()
    assert downloads == [("https://example.test/kws.tar.bz2", tmp_path / "models" / f"{model_name}.tar.bz2")]
    assert (model_dir / "xiaoban_keywords.txt").read_text(encoding="utf-8") == (
        "x iǎo b àn x iǎo b àn @小伴小伴\n"
    )


def test_ensure_keyword_spotter_model_prefers_local_archive_before_download(tmp_path):
    source_root = tmp_path / "source"
    model_name = "sherpa-onnx-kws-test"
    local_archive = tmp_path / "packaged.tar.bz2"
    model_source = source_root / model_name
    model_source.mkdir(parents=True)
    for name in (
        "tokens.txt",
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    ):
        (model_source / name).write_text("stub", encoding="utf-8")
    (model_source / "keywords.txt").write_text("old keyword\n", encoding="utf-8")
    with tarfile.open(local_archive, mode="w:bz2") as tar:
        tar.add(model_source, arcname=model_name)

    def fail_downloader(_url: str, _target: Path) -> None:
        raise AssertionError("download should not be used when local archive exists")

    model_dir = tmp_path / "models" / model_name

    ensure_keyword_spotter_model(
        model_dir,
        model_url="https://example.test/kws.tar.bz2",
        local_archive=local_archive,
        downloader=fail_downloader,
    )

    assert KeywordSpotterConfig.from_model_dir(model_dir).exists()
    assert (model_dir / "xiaoban_keywords.txt").exists()
