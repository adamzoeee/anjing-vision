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

    # 限制 SIFT 线程与输入尺寸：Windows 上默认 24 线程提取 1080p 大图
    # 易触发 pycolmap C++ 访问冲突（0xc0000005）拖垮整个 solo worker
    options = pycolmap.FeatureExtractionOptions()
    options.num_threads = 8
    options.max_image_size = 1600
    # 指定相机内参：视频帧无 EXIF 焦距，默认 1.2×max(w,h) 对手机广角视频偏长焦，
    # 几何验证不会产生 CALIBRATED 对，初始图像对永远找不到（No good initial pair）。
    # 按广角先验取 0.75×max(w,h)，后续 A4 标定/门高先验会修正尺度。
    sample = next(iter(image_dir.glob("*.jpg")))
    import cv2
    probe = cv2.imread(str(sample))
    reader = None
    if probe is not None:
        h, w = probe.shape[:2]
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = "SIMPLE_RADIAL"
        reader.camera_params = f"{0.75 * max(w, h):.1f},{w / 2:.1f},{h / 2:.1f},0"
    if reader is None:
        # 无法读取参考帧（异常输入/测试环境）：回退默认，由 pycolmap 自行估计内参
        pycolmap.extract_features(db_path, str(image_dir), extraction_options=options)
    else:
        pycolmap.extract_features(
            db_path, str(image_dir),
            reader_options=reader, extraction_options=options,
        )
    pycolmap.match_exhaustive(db_path)
    maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 4.x 返回 {model_index: Reconstruction}（失败为空 dict）；
    # 多模型时取注册帧最多的主模型；兼容旧版本返回列表/元组或 None
    if isinstance(maps, dict):
        recons = [m for m in maps.values() if m is not None]
        recon = max(recons, key=lambda r: len(r.images)) if recons else None
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
        pose = img.cam_from_world()  # pycolmap 4.x: 方法，返回 Rigid3d（世界→相机）
        R = np.asarray(pose.rotation.matrix(), dtype=np.float64)  # 3x3
        t = np.asarray(pose.translation, dtype=np.float64).reshape(3)
        c = -R.T @ t
        fx = cam.focal_length_x
        fy = cam.focal_length_y
        cx = cam.principal_point_x
        cy = cam.principal_point_y
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        cameras.append({"name": img.name, "R": R, "t": t, "K": K, "center": c})
    for pid in recon.points3D:
        points.append(recon.points3D[pid].xyz)
    return {
        "cameras": cameras,
        "points3D": np.asarray(points, dtype=np.float64).reshape(-1, 3),
        "model_path": model_path,
    }
