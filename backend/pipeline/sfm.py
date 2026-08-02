"""SFM：pycolmap 从图片集恢复相机位姿与稀疏点云。"""
import shutil
from pathlib import Path

import numpy as np

OUTPUT_DIR = "sfm"


def build_synthetic_cameras(n: int = 12, radius: float = 2.0, height: float = 1.5):
    """合成绕圈相机位姿（用于测试与调试）。返回 [{center, R, K}]。"""
    cams = []
    for i in range(n):
        theta = 2 * np.pi * i / n
        center = np.array([radius * np.cos(theta), radius * np.sin(theta), height])
        forward = -center.copy()
        forward[2] = 0.0
        forward /= np.linalg.norm(forward)
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, up)
        R = np.stack([right, np.cross(up, right), up], axis=1)
        K = np.array([[600.0, 0, 320], [0, 600, 240], [0, 0, 1.0]])
        cams.append({"center": center, "R": R, "K": K})
    return cams


def run_sfm(image_dir: Path, work_dir: Path) -> dict:
    """对 image_dir 内 jpg 运行增量式 SFM。

    返回 {"cameras": [...], "points3D": np.ndarray (N,3), "model_path": Path}。
    相机坐标系为 COLMAP 约定（camera-to-world 的逆）。
    """
    image_dir = Path(image_dir)
    if not image_dir.exists() or not list(image_dir.glob("*.jpg")):
        raise FileNotFoundError(f"图片目录不存在或没有 jpg: {image_dir}")
    work_dir = Path(work_dir)
    db_path = work_dir / "database.db"
    model_path = work_dir / OUTPUT_DIR
    for p in (db_path, model_path):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    import pycolmap

    pycolmap.extract_features(db_path, str(image_dir))
    pycolmap.match_exhaustive(db_path)
    maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    if not maps or not maps[0]:
        raise RuntimeError("SFM 失败：无法恢复相机位姿（图片过少或纹理不足）")
    recon: pycolmap.Reconstruction = maps[0]
    cameras, points = [], []
    for img_id in recon.images:
        img = recon.images[img_id]
        cam = recon.cameras[img.camera_id]
        pose = img.cam_from_world  # pycolmap 4.x: Rigid3d（世界→相机）
        R = np.asarray(pose.rotation().matrix(), dtype=np.float64)  # 3x3
        t = np.asarray(pose.translation(), dtype=np.float64).reshape(3)
        c = -R.T @ t
        fx = cam.focal_length_x()
        fy = cam.focal_length_y()
        cx, cy = cam.principal_point_x(), cam.principal_point_y()
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        cameras.append({"name": img.name, "R": R, "t": t, "K": K, "center": c})
    for pid in recon.points3D:
        points.append(recon.points3D[pid].xyz)
    return {
        "cameras": cameras,
        "points3D": np.asarray(points, dtype=np.float64).reshape(-1, 3),
        "model_path": model_path,
    }
