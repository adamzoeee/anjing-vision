import numpy as np
import pytest
import torch

from pipeline.semantic import (
    build_prompt,
    clamp_bbox,
    merge_votes,
    normalize_prompt_label,
    postprocess_detections,
    project_mask_to_points,
    segment_detections,
)


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


def test_prompt_and_label_mapping_are_grounding_dino_compatible():
    prompt = build_prompt()
    assert prompt.endswith(".")
    assert "cardboard box." in prompt
    assert normalize_prompt_label("cardboard box storage box") == "纸箱"
    assert normalize_prompt_label("potted plant") == "盆栽"
    assert normalize_prompt_label("indoor plant") == "盆栽"
    assert normalize_prompt_label("cat") == "宠物"
    assert normalize_prompt_label("plastic bucket") == "水桶"
    assert normalize_prompt_label("unrelated") is None


def test_clamp_bbox_rejects_invalid_and_clips_to_pixels():
    assert clamp_bbox([-5, 2, 120, 70], (60, 100, 3)) == [0.0, 2.0, 99.0, 59.0]
    assert clamp_bbox([10, 10, 10, 20], (60, 100, 3)) is None
    assert clamp_bbox([np.nan, 0, 1, 1], (60, 100, 3)) is None


def test_postprocess_detections_maps_labels_clips_and_suppresses_duplicates():
    raw = {
        "scores": torch.tensor([0.9, 0.8, 0.7]),
        "labels": ["chair", "chair", "unknown"],
        "boxes": torch.tensor([[-5, 0, 55, 55], [0, 0, 54, 54], [1, 1, 2, 2]]),
    }
    results = postprocess_detections(raw, (50, 50, 3), nms_iou_threshold=0.5)
    assert len(results) == 1
    assert results[0]["label"] == "椅子"
    assert results[0]["bbox"] == [0.0, 0.0, 49.0, 49.0]


def test_postprocess_detections_deduplicates_overlapping_container_synonyms():
    results = postprocess_detections(
        {
            "scores": torch.tensor([0.82, 0.79, 0.75]),
            "labels": ["storage box", "cardboard box", "chair"],
            "boxes": torch.tensor(
                [
                    [10.0, 10.0, 90.0, 90.0],
                    [10.5, 10.5, 90.5, 90.5],
                    [10.0, 10.0, 90.0, 90.0],
                ]
            ),
        },
        (100, 100, 3),
    )

    assert [item["label"] for item in results] == ["收纳箱", "椅子"]


def test_segment_detections_reuses_one_image_embedding_for_multiple_boxes(monkeypatch):
    class Transform:
        @staticmethod
        def apply_boxes_torch(boxes, _shape):
            return boxes

    class Predictor:
        def __init__(self):
            self.model = torch.nn.Linear(1, 1)
            self.transform = Transform()
            self.set_image_calls = 0
            self.shape = None

        def set_image(self, image):
            self.set_image_calls += 1
            self.shape = image.shape[:2]

        def predict_torch(self, *, boxes, **_kwargs):
            masks = torch.zeros((len(boxes), 1, *self.shape), dtype=torch.bool)
            masks[:, :, 2:8, 3:9] = True
            return masks, torch.full((len(boxes), 1), 0.95), None

    predictor = Predictor()
    monkeypatch.setattr("pipeline.semantic._load_sam", lambda: predictor)
    image = np.zeros((10, 12, 3), dtype=np.uint8)
    detections = [
        {"bbox": [1, 1, 8, 8], "label": "椅子", "score": 0.8},
        {"bbox": [2, 2, 10, 9], "label": "桌子", "score": 0.7},
    ]
    results = segment_detections(image, detections, batch_size=2)
    assert predictor.set_image_calls == 1
    assert len(results) == 2
    assert all(result["mask"].shape == image.shape[:2] for result in results)
    assert all(result["mask_valid"] for result in results)
    assert all(result["mask_area_px"] == 36 for result in results)


def test_segment_detections_handles_empty_detections_without_loading_sam(monkeypatch):
    monkeypatch.setattr(
        "pipeline.semantic._load_sam",
        lambda: pytest.fail("SAM should not load for an empty detection list"),
    )
    assert segment_detections(np.zeros((8, 9, 3), dtype=np.uint8), []) == []


def test_segment_detections_marks_empty_mask_invalid(monkeypatch):
    class Transform:
        @staticmethod
        def apply_boxes_torch(boxes, _shape):
            return boxes

    class Predictor:
        def __init__(self):
            self.model = torch.nn.Linear(1, 1)
            self.transform = Transform()

        def set_image(self, image):
            self.shape = image.shape[:2]

        def predict_torch(self, *, boxes, **_kwargs):
            masks = torch.zeros((len(boxes), 1, *self.shape), dtype=torch.bool)
            return masks, torch.full((len(boxes), 1), 0.1), None

    monkeypatch.setattr("pipeline.semantic._load_sam", lambda: Predictor())
    results = segment_detections(
        np.zeros((8, 9, 3), dtype=np.uint8),
        [{"bbox": [1, 1, 6, 6], "label": "杂物", "score": 0.5}],
    )

    assert results[0]["mask"].shape == (8, 9)
    assert results[0]["mask_area_px"] == 0
    assert results[0]["mask_valid"] is False


def test_project_mask_validates_shapes_and_merge_votes_is_deterministic():
    with pytest.raises(ValueError, match="Nx3"):
        project_mask_to_points(np.zeros((3, 2)), np.zeros((4, 4)), np.eye(3), np.eye(3), np.zeros(3))
    assert merge_votes({1: {}, 2: {"桌子": 2, "椅子": 2}, 3: {"纸箱": 0}}) == {2: "桌子"}
