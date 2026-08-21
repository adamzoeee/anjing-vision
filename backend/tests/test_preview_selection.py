import json
from pathlib import Path

from app.routers.preview import _selected_preview_ply


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ply")


def test_preview_selection_defaults_to_original_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "postprocess" / "scene_preview.ply"
    _touch(baseline)
    assert _selected_preview_ply(tmp_path) == baseline.resolve()


def test_preview_selection_requires_explicit_existing_ply(tmp_path: Path) -> None:
    postprocess = tmp_path / "postprocess"
    baseline = postprocess / "scene_preview.ply"
    accepted = postprocess / "scene_preview_baseline45.ply"
    _touch(baseline)
    _touch(accepted)
    (postprocess / "preview_selection.json").write_text(
        json.dumps({"accepted_file": accepted.name}), encoding="utf-8"
    )
    assert _selected_preview_ply(tmp_path) == accepted.resolve()


def test_preview_selection_rejects_path_escape(tmp_path: Path) -> None:
    postprocess = tmp_path / "postprocess"
    baseline = postprocess / "scene_preview.ply"
    outside = tmp_path / "outside.ply"
    _touch(baseline)
    _touch(outside)
    (postprocess / "preview_selection.json").write_text(
        json.dumps({"accepted_file": "../outside.ply"}), encoding="utf-8"
    )
    assert _selected_preview_ply(tmp_path) == baseline.resolve()
