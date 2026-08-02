import numpy as np
from pipeline.semantic import project_mask_to_points, merge_votes


def test_project_mask_to_points_basic():
    # 相机中心在世界 (0,0,2)（t=(0,0,-2)，R=eye），正对 z 轴；
    # 点 (0,0,4) 深度为 2 → 投影 (320,240) 落于 mask 中心 → 命中；
    # 点 (1,1,4) 投影 (570,490) 在 mask 外 → 不命中。
    K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.0]])
    R, t = np.eye(3), np.array([0, 0, -2.0])
    pts = np.array([[0.0, 0.0, 4.0], [1.0, 1.0, 4.0]])
    mask = np.zeros((480, 640), dtype=np.uint8)
    mask[240 - 25:240 + 25, 320 - 25:320 + 25] = 1
    hits = project_mask_to_points(pts, mask, K, R, t)
    assert 0 in hits and 1 not in hits


def test_merge_votes_majority():
    votes = {0: {"杂物": 3, "家具": 1}, 1: {"家具": 5}}
    labels = merge_votes(votes)
    assert labels[0] == "杂物" and labels[1] == "家具"


def test_project_mask_negative_depth_ignored():
    """相机后方点（深度<=0）必须被忽略。"""
    K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.0]])
    R, t = np.eye(3), np.zeros(3)  # 相机在世界原点，正对 z 轴
    pts = np.array([[0.0, 0.0, -1.0]])  # 点在相机后方（cam z=-1）
    mask = np.zeros((480, 640), dtype=np.uint8)
    mask[240 - 25:240 + 25, 320 - 25:320 + 25] = 1
    hits = project_mask_to_points(pts, mask, K, R, t)
    assert hits == []
