import json
from pathlib import Path

from app.routers.preview import _verified_point_preview


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ply")


def test_unaccepted_high_point_candidate_never_replaces_baseline(tmp_path: Path) -> None:
    postprocess = tmp_path / "postprocess"
    baseline = postprocess / "scene_preview.ply"
    experimental = postprocess / "scene_preview_video_completed.ply"
    _touch(baseline)
    _touch(experimental)
    experimental.with_suffix(".json").write_text(
        json.dumps({
            "display_only": True,
            "excluded_from_measurement_and_risk": True,
            "registration_validation": "passed",
            "output_points": 2_500_000,
        }),
        encoding="utf-8",
    )
    assert _verified_point_preview(tmp_path) == baseline.resolve()


def test_explicit_selection_can_choose_existing_ply(tmp_path: Path) -> None:
    postprocess = tmp_path / "postprocess"
    baseline = postprocess / "scene_preview.ply"
    accepted = postprocess / "scene_preview_baseline45.ply"
    _touch(baseline)
    _touch(accepted)
    (postprocess / "preview_selection.json").write_text(
        json.dumps({"accepted_file": accepted.name}), encoding="utf-8"
    )
    assert _verified_point_preview(tmp_path) == accepted.resolve()


def test_explicit_selection_rejects_path_escape(tmp_path: Path) -> None:
    postprocess = tmp_path / "postprocess"
    baseline = postprocess / "scene_preview.ply"
    outside = tmp_path / "outside.ply"
    _touch(baseline)
    _touch(outside)
    (postprocess / "preview_selection.json").write_text(
        json.dumps({"accepted_file": "../outside.ply"}), encoding="utf-8"
    )
    assert _verified_point_preview(tmp_path) == baseline.resolve()
