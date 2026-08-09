"""pipeline_runner 的标定逻辑单元测试（不依赖 GPU/DB）。"""
import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Organization, Project, Report, Scan
from app.tasks.pipeline_runner import (
    _calibrate_with_a4,
    _find_obstacles,
    _pixel_ray,
    _triangulate,
    _upsert_report,
)


def _cam(center, look_at, focal=600.0, w=640, h=480):
    """构造针孔相机：位置 center，看向 look_at。返回 {R, t, K}（world→cam 约定）。"""
    z = np.asarray(look_at, dtype=float) - np.asarray(center, dtype=float)
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 0.0, 1.0])
    if abs(z @ up) > 0.99:  # 光轴与 up 平行时换参考轴
        up = np.array([1.0, 0.0, 0.0])
    x = np.cross(up, z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    R_w2c = np.stack([x, y, z], axis=0)  # world→cam
    C = np.asarray(center, dtype=float)
    t = -R_w2c @ C
    K = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.0]])
    return {"R": R_w2c, "t": t, "K": K}


def test_pixel_ray_unit_norm():
    cam = _cam([0, 0, 0], [0, 0, -1])
    ray = _pixel_ray(cam, 320, 240)
    assert abs(np.linalg.norm(ray) - 1.0) < 1e-9


def test_triangulate_converging_rays():
    # 两条从不同起点指向同一点的射线 → 最近点≈目标
    target = np.array([0.0, 0.0, 2.0])
    C_i = np.array([0.5, 0.0, 0.0])
    C_j = np.array([-0.5, 0.0, 0.0])
    d_i = (target - C_i) / np.linalg.norm(target - C_i)
    d_j = (target - C_j) / np.linalg.norm(target - C_j)
    P = _triangulate(C_i, d_i, C_j, d_j)
    assert P is not None
    assert np.allclose(P, target, atol=1e-6)


def test_triangulate_parallel_rays():
    d = np.array([0.0, 0.0, 1.0])
    assert _triangulate(np.array([0.0, 0.0, 0.0]), d, np.array([1.0, 0.0, 0.0]), d) is None


def test_calibrate_with_a4_two_views(monkeypatch):
    """两帧看到同一 A4（画面中心、真实距离 2.0m、SFM 单位距离 1.0）→ 尺度≈2.0。"""
    # SFM 场景：A4 在原点，相机距离 1 单位（米制深度 2.0m → 尺度≈2.0 米/单位）
    a4_pos = np.array([0.0, 0.0, 0.0])
    cams = [
        _cam([0.3, 0.0, 1.0], a4_pos),
        _cam([-0.3, 0.0, 1.0], a4_pos),
    ]
    imgs = [np.zeros((480, 640, 3), dtype=np.uint8) for _ in cams]

    def fake_detect(img):
        # 焦距 600、真实距离 2.0m → 长边像素 = 600 * 0.297 / 2.0 = 89.1
        long_px = 600 * 0.297 / 2.0
        return (long_px, long_px / 1.414, 320.0, 240.0)  # (长, 短, cx, cy)

    monkeypatch.setattr("pipeline.calibrator.detect_a4_in_image", fake_detect)
    scale = _calibrate_with_a4(imgs, cams)
    assert scale is not None
    # 米制深度 2.0 / SFM 单位距离 ~1.04 → 尺度 ~1.9
    assert 1.5 < scale < 2.5


def test_report_write_is_idempotent_for_pipeline_retries():
    db = SessionLocal()
    try:
        organization = Organization(name="幂等测试机构")
        db.add(organization)
        db.flush()
        project = Project(org_id=organization.id, name="测试项目")
        db.add(project)
        db.flush()
        scan = Scan(project_id=project.id, status="reporting")
        db.add(scan)
        db.commit()

        _upsert_report(
            db,
            scan_id=scan.id,
            score=60,
            risks=[{"code": "old"}],
            measures={"door_width_m": 0.7},
            advice=["旧建议"],
            images=["old.png"],
            preview={"path": "old"},
            calibrated=1,
        )
        db.commit()
        first_report_id = db.query(Report).filter_by(scan_id=scan.id).one().id

        _upsert_report(
            db,
            scan_id=scan.id,
            score=88,
            risks=[{"code": "new"}],
            measures={"door_width_m": 0.9},
            advice=["新建议"],
            images=["new.png"],
            preview={"path": "new"},
            calibrated=2,
        )
        db.commit()

        reports = db.query(Report).filter_by(scan_id=scan.id).all()
        assert len(reports) == 1
        assert reports[0].id == first_report_id
        assert reports[0].score == 88
        assert reports[0].risks == [{"code": "new"}]
        assert reports[0].measures == {"door_width_m": 0.9}
        assert reports[0].advice == ["新建议"]
        assert reports[0].images == ["new.png"]
        assert reports[0].preview == {"path": "new"}
        assert reports[0].calibrated == 2
    finally:
        db.close()


def test_report_write_recovers_from_concurrent_unique_conflict():
    existing = Report(scan_id=9, score=40, risks=[])

    class QueryResult:
        def __init__(self, value):
            self.value = value

        def filter(self, *_args):
            return self

        def one_or_none(self):
            return self.value

    class ConcurrentSession:
        def __init__(self):
            self.query_results = iter([None, existing])
            self.rolled_back = False

        def query(self, _model):
            return QueryResult(next(self.query_results))

        def add(self, _report):
            pass

        def flush(self):
            raise IntegrityError("insert report", {}, Exception("unique"))

        def rollback(self):
            self.rolled_back = True

    db = ConcurrentSession()

    report = _upsert_report(
        db,
        scan_id=9,
        score=91,
        risks=[{"code": "updated"}],
        measures={"door_width_m": 1.0},
        advice=["更新建议"],
        images=["updated.png"],
        preview={"path": "updated"},
        calibrated=2,
    )

    assert db.rolled_back is True
    assert report is existing
    assert report.score == 91
    assert report.risks == [{"code": "updated"}]


def test_find_obstacles_consumes_sam_masks_without_declaring_2d_furniture_a_risk(monkeypatch):
    mask = np.zeros((20, 20), dtype=bool)
    mask[8:13, 8:13] = True

    def fake_analyze(_image):
        return [{
            "label": "椅子",
            "score": 0.9,
            "bbox": [8, 8, 12, 12],
            "mask": mask,
            "mask_valid": True,
            "mask_area_ratio": float(mask.mean()),
        }]

    monkeypatch.setattr("pipeline.semantic.analyze_image", fake_analyze)
    camera = {
        "K": np.array([[10.0, 0, 10.0], [0, 10.0, 10.0], [0, 0, 1.0]]),
        "R": np.eye(3),
        "t": np.zeros(3),
    }
    points = np.array([[0.0, 0.0, 2.0], [10.0, 10.0, 2.0]])
    result = _find_obstacles(
        [np.zeros((20, 20, 3), dtype=np.uint8)], [camera], points, frame_stride=1
    )
    assert result["detected_objects"] == [{
        "label": "椅子",
        "count": 1,
        "segmented_count": 1,
        "frame_count": 1,
        "mean_mask_area_ratio": 0.0625,
        "projected_point_count": 1,
    }]
    assert result["semantic_point_counts"] == {"椅子": 1}
    assert result["obstacles_in_passage"] == []
