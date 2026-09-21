import json
from pathlib import Path
import zipfile

from scripts.build_scan46_research_bundle import build_bundle


def test_bundle_only_contains_delta_and_source_videos(tmp_path: Path):
    backend = tmp_path / "backend"
    work = backend / "data/work/46/postprocess"
    target = backend / "data/work/46/local_gaps_20260912/target_r2_20260914/slam3r"
    media = backend / "data/media/46"
    videos = tmp_path / "buchong2"
    work.mkdir(parents=True)
    target.mkdir(parents=True)
    media.mkdir(parents=True)
    videos.mkdir()
    (work / "preview_selection.json").write_bytes(b"new")
    (work / "local_gaps_20260912.ply").write_bytes(b"same")
    (work / "local_gaps_20260912.visual.json").write_bytes(b"visual")
    (target / "new.npy").write_bytes(b"only-new")
    (media / "base.mp4").write_bytes(b"base-video")
    (videos / "窗户边角2.mp4").write_bytes(b"supplement-video")

    base = tmp_path / "base.zip"
    supplement = tmp_path / "supplement.zip"
    window = tmp_path / "window.zip"
    with zipfile.ZipFile(base, "w") as archive:
        archive.writestr("data/work/46/postprocess/local_gaps_20260912.ply", b"same")
        archive.writestr("data/work/46/postprocess/preview_selection.json", b"old")
        archive.writestr("data/media/46/base.mp4", b"base-video")
    with zipfile.ZipFile(supplement, "w") as archive:
        archive.writestr("data/work/46/selection.json", b"old-selection")
    with zipfile.ZipFile(window, "w") as archive:
        archive.writestr("data/work/46/window.ply", b"window")

    output = tmp_path / "research.zip"
    result = build_bundle(backend, videos, [base, supplement, window], output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "data/work/46/postprocess/local_gaps_20260912.ply" not in names
        assert "data/media/46/base.mp4" not in names
        assert "data/work/46/postprocess/preview_selection.json" in names
        assert "data/work/46/local_gaps_20260912/target_r2_20260914/slam3r/new.npy" in names
        assert "data/training_sources/scan46_buchong2/窗户边角2.mp4" in names
        manifest = json.loads(archive.read("scan46_research_bundle_manifest.json"))
        assert manifest["apply_after"] == [base.name, supplement.name, window.name]
        assert archive.testzip() is None
    assert len(result["skipped_identical_entries"]) == 1
