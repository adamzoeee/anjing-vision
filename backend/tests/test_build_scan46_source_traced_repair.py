from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_scan46_source_traced_repair import (
    accept_candidate,
    gap_occlusion_guard,
    sample_observed_mesh,
    traced_jamb_ids,
)


def test_traced_jamb_ids_requires_exact_pixels_inside_interior():
    lineage = {
        "source_uv": np.array([[87.0, 25.0], [87.0, 25.0], [90.0, 25.0], [84.0, 191.0]]),
        "exact": np.array([True, False, True, True]),
        "baseline_ids": np.array([7, 8, 9, 10]),
    }
    assert traced_jamb_ids(lineage).tolist() == [7]


def test_gap_guard_keeps_gap_point_and_rejects_foreground_occluder(monkeypatch):
    import build_scan46_source_traced_repair as subject

    views = {"front": object()}
    monkeypatch.setattr(subject, "VIEWS", views)
    old = np.full((896, 896), np.inf)
    old[100, 100] = 2.0
    monkeypatch.setattr(subject, "depth_buffer", lambda points, view: old)

    def fake_project(points, view):
        xy = np.array([[200, 200], [100, 100], [100, 100]])
        depth = np.array([1.0, 1.9, 2.01])
        return xy, depth, np.ones(3, dtype=bool)

    monkeypatch.setattr(subject, "project", fake_project)
    keep, audit = gap_occlusion_guard(np.zeros((3, 3)), np.zeros((1, 3)))
    assert keep.tolist() == [True, False, False]
    assert audit["front"]["gap"] == 1
    assert audit["front"]["foreground_rejected"] == 1


def test_mesh_sampling_stays_inside_observed_triangles():
    points = np.array([[0.0, 0.0, 0.0], [0.012, 0.0, 0.0], [0.0, 0.012, 0.0]])
    uv = points[:, :2]
    sampled, sampled_uv = sample_observed_mesh(
        points, uv, np.array([[0, 1, 2]]), spacing_m=0.006
    )
    assert sampled.shape == (10, 3)
    assert sampled_uv.shape == (10, 2)
    assert np.all(sampled[:, 0] >= 0.0)
    assert np.all(sampled[:, 1] >= 0.0)
    assert np.all(sampled[:, 0] + sampled[:, 1] <= 0.012 + 1e-12)


def test_accept_candidate_backs_up_selection_and_rejects_research_mode(tmp_path, monkeypatch):
    import json
    import build_scan46_source_traced_repair as subject

    monkeypatch.setattr(subject, "POST", tmp_path)
    selection = tmp_path / "preview_selection.json"
    selection.write_text('{"accepted_file":"baseline.ply"}', encoding="utf-8")
    output = tmp_path / "repair.ply"
    output.write_bytes(b"ply")
    report = {"include_observed_r3": False, "observed_points_added": 0}
    accept_candidate(output, report)
    assert json.loads(selection.read_text(encoding="utf-8"))["accepted_file"] == "repair.ply"
    assert (tmp_path / "preview_selection.before_source_traced_repair.json").read_text(
        encoding="utf-8"
    ) == '{"accepted_file":"baseline.ply"}'

    report["include_observed_r3"] = True
    try:
        accept_candidate(output, report)
    except RuntimeError as exc:
        assert "research additions" in str(exc)
    else:
        raise AssertionError("research mode must never change the accepted preview")
