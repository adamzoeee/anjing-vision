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
        # 针孔约定：x 右、y 下、z 前（光轴=forward 指向圆心）；列 = [x_cam, y_cam, z_cam]
        R = np.stack([right, -up, forward], axis=1)
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
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "database.db"
    model_path = work_dir / OUTPUT_DIR
    for p in (db_path, model_path):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    import pycolmap

    pycolmap.extract_features(db_path, str(image_dir))
    pycolmap.match_exhaustive(db_path)
    maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 4.x 返回 dict（失败时空 dict）；兼容旧版本返回列表/元组或 None 的情况
    if isinstance(maps, dict):
        recon = maps.get("reconstruction")
    elif isinstance(maps, (list, tuple)):
        recon = maps[0] if maps else None
    else:
        recon = maps
    if recon is None:
        raise RuntimeError("SFM 失败：无法恢复相机位姿（图片过少或纹理不足）")
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
