"""预览清单的扫描级“整体框架校准”字段（手调参数固化）。"""
import json
import os
from pathlib import Path


def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": "a2@x.com", "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": "a2@x.com", "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_postprocess(scan_id: int) -> Path:
    return Path(os.environ["DATA_DIR"]) / "work" / str(scan_id) / "postprocess"


def test_manifest_exposes_frame_calibration(client) -> None:
    """清单只在参数文件存在时给出 URL；缺文件时其他扫描不受影响（None + 404）。"""
    headers = _auth(client)
    project = client.post("/api/projects", json={"name": "框架校准"}, headers=headers).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans", json={"capture_type": "video"}, headers=headers
    ).json()
    post = _scan_postprocess(scan["id"])
    post.mkdir(parents=True, exist_ok=True)
    baseline = post / "scene_preview.ply"
    baseline.write_bytes(b"ply\n")
    calibration = post / "frame_calibration.json"
    calibration.write_text(json.dumps({
        "source": "scan46-frame-calibration",
        "scan_id": scan["id"],
        "transform": {
            "yaw_deg": 180, "scale_x": 0.84, "scale_y": 0.71, "scale_z": 0.9,
            "translate_x": 1.54, "translate_y": 1.64, "translate_z": 0,
        },
        "cloud_frame": {"floor_y_pack": 1.3449620543191287},
    }, ensure_ascii=False), encoding="utf-8")
    try:
        manifest = client.get(f"/api/preview/{scan['id']}/manifest.json", headers=headers).json()
        assert manifest["frame_calibration"] == f"/api/preview/{scan['id']}/frame_calibration.json"
        response = client.get(manifest["frame_calibration"], headers=headers)
        assert response.status_code == 200
        assert response.json()["transform"]["scale_x"] == 0.84
        assert response.json()["cloud_frame"]["floor_y_pack"] == 1.3449620543191287
        assert "no-store" in response.headers.get("cache-control", "")

        calibration.unlink()
        manifest = client.get(f"/api/preview/{scan['id']}/manifest.json", headers=headers).json()
        assert manifest["frame_calibration"] is None
        missing = client.get(f"/api/preview/{scan['id']}/frame_calibration.json", headers=headers)
        assert missing.status_code == 404
    finally:
        for path in (calibration, baseline):
            if path.exists():
                path.unlink()
