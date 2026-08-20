import importlib.util
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gaussian" / "recover_poses.py"
SPEC = importlib.util.spec_from_file_location("recover_poses_selection", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _candidate(frame_id: int, reproj: float = 1.0):
    return (frame_id, None, None, np.arange(1200), 280.0, reproj)


def test_selection_covers_full_timeline_and_keeps_limit():
    collected = [_candidate(i) for i in range(1000)]
    images = np.zeros((1000, 224, 224, 3), dtype=np.uint8)
    images[:, :, 112:, :] = 255
    primary, backups = MODULE.select_distributed_candidates(collected, images, 600)
    ids = [item[0] for item in primary]
    assert len(primary) == 600
    assert len(backups) == 400
    assert min(ids) <= 1
    assert max(ids) >= 998
    assert ids == sorted(ids)


def test_selection_prefers_sharper_candidate_inside_time_bucket():
    collected = [_candidate(i) for i in range(10)]
    images = np.zeros((10, 224, 224, 3), dtype=np.uint8)
    images[1, ::2, :, :] = 255
    images[6, ::2, :, :] = 255
    primary, _ = MODULE.select_distributed_candidates(collected, images, 5)
    ids = [item[0] for item in primary]
    assert 1 in ids
    assert 6 in ids


def test_selection_does_not_drop_anything_under_limit():
    collected = [_candidate(i) for i in range(30)]
    primary, backups = MODULE.select_distributed_candidates(collected, None, 600)
    assert primary == collected
    assert backups == []


def test_four_minute_video_keeps_all_1015_valid_views_under_8gb_cap():
    collected = [_candidate(i) for i in range(1015)]
    primary, backups = MODULE.select_distributed_candidates(collected, None, 1024)
    assert len(primary) == 1015
    assert primary[0][0] == 0
    assert primary[-1][0] == 1014
    assert backups == []


def test_cli_and_training_keep_hard_1024_view_guard():
    recover_source = MODULE_PATH.read_text(encoding="utf-8")
    train_source = (MODULE_PATH.parent / "train_gsplat.py").read_text(encoding="utf-8")
    runner_source = (MODULE_PATH.parents[2] / "pipeline" / "gaussian_runner.py").read_text(encoding="utf-8")
    assert "min(max(args.max_frames, 30), MAX_SAFE_VIEWS_8GB)" in recover_source
    assert "MAX_SAFE_VIEWS_8GB = 1024" in train_source
    assert "MAX_SAFE_VIEWS_8GB = 1024" in runner_source
    assert "dtype=np.uint8" in train_source
    assert ".cuda().float().div_(255.0)" in train_source
