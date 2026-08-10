"""SFM：pycolmap 从图片集恢复相机位姿与稀疏点云。"""
import shutil
from pathlib import Path

import numpy as np

OUTPUT_DIR = "sfm"


def undistort_registered_view(image: np.ndarray, camera: dict) -> tuple[np.ndarray, dict]:
    """把 COLMAP 径向畸变图转换为针孔图，并保留同步内参。

    当前视频统一使用 SIMPLE_RADIAL。保持输出尺寸和 K 不变可避免裁剪视野，
    同时让训练、语义投影和浏览器预览共享同一套针孔坐标约定。
    """
    model = str(camera.get("camera_model", "PINHOLE")).upper()
    radial = np.asarray(camera.get("radial_distortion", []), dtype=np.float64)
    result_camera = dict(camera)
    result_camera["K"] = np.asarray(camera["K"], dtype=np.float64).copy()
    if model not in {"SIMPLE_RADIAL", "RADIAL"} or radial.size == 0 or not np.any(radial):
        result_camera["undistorted"] = model in {"PINHOLE", "SIMPLE_PINHOLE"}
        return np.asarray(image).copy(), result_camera

    import cv2

    coefficients = np.zeros(5, dtype=np.float64)
    coefficients[0] = radial[0]
    if radial.size > 1:
        coefficients[1] = radial[1]
    rectified = cv2.undistort(
        np.asarray(image), result_camera["K"], coefficients, None, result_camera["K"]
    )
    result_camera["source_camera_model"] = model
    result_camera["source_radial_distortion"] = radial.copy()
    result_camera["camera_model"] = "PINHOLE"
    result_camera["radial_distortion"] = np.zeros_like(radial)
    result_camera["undistorted"] = True
    return rectified, result_camera


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
            # 同一视频的所有帧来自同一个手机镜头，必须共享相机内参；AUTO 会在
            # 无 EXIF 的抽帧图片上为每帧创建独立 Camera，导致位姿恢复严重不稳。
            camera_mode=pycolmap.CameraMode.SINGLE,
            reader_options=reader, extraction_options=options,
        )
    image_count = len(list(image_dir.glob("*.jpg")))
    # 输入来自按时间排序的单段视频。全量两两匹配会随帧数平方增长，并容易让
    # 室内重复纹理/窗外画面产生远距离误匹配。先覆盖相邻 15 帧；若轨迹仍被
    # 分成多个局部模型，再扩大到 30 帧重试，避免所有视频都承担高匹配开销。
    # quadratic_overlap 额外连接较远邻帧，既保留连续轨迹又控制错误匹配。
    pairing = pycolmap.SequentialPairingOptions()
    pairing.overlap = 15
    pairing.quadratic_overlap = True
    pairing.num_threads = 8
    pycolmap.match_sequential(db_path, pairing_options=pairing)
    maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    initial_models = (
        [model for model in maps.values() if model is not None]
        if isinstance(maps, dict)
        else list(maps) if isinstance(maps, (list, tuple)) else []
    )
    if len(initial_models) > 1:
        shutil.rmtree(model_path, ignore_errors=True)
        pairing.overlap = 30
        pycolmap.match_sequential(db_path, pairing_options=pairing)
        maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 两轮顺序匹配仍可能在快速转向或短暂模糊处把同一房间切成多个局部模型。
    # 不能因为最大局部模型碰巧存在就直接宣告失败：仅当主模型注册率不足质量
    # 门槛时，追加全量匹配并重新建图。视频帧已限制为最多 240 张，因此该兜底
    # 成本有上界；正常连续视频仍只走较快的顺序匹配。
    models_after_sequential = _reconstructions(maps)
    component_sizes = sorted(
        (len(model.images) for model in models_after_sequential), reverse=True
    )
    best_registered = component_sizes[0] if component_sizes else 0
    significant_secondary = len(component_sizes) > 1 and component_sizes[1] >= max(
        10, int(np.ceil(image_count * 0.10))
    )
    if best_registered / max(image_count, 1) < 0.70 or significant_secondary:
        shutil.rmtree(model_path, ignore_errors=True)
        pycolmap.match_exhaustive(db_path)
        maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 4.x 返回 {model_index: Reconstruction}（失败为空 dict）；
    # 多模型时取注册帧最多的主模型；兼容旧版本返回列表/元组或 None
    recons = _reconstructions(maps)
    recon = max(recons, key=lambda r: len(r.images)) if recons else None
    if recon is None:
        raise RuntimeError("SFM 失败：无法恢复相机位姿（图片过少或纹理不足）")
    cameras, points, colors, errors, track_lengths = [], [], [], [], []
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
        model_name = getattr(getattr(cam, "model", None), "name", None) or str(
            getattr(cam, "model_name", "PINHOLE")
        )
        params = np.asarray(getattr(cam, "params", []), dtype=np.float64)
        radial = np.zeros(0, dtype=np.float64)
        if model_name == "SIMPLE_RADIAL" and params.size >= 4:
            radial = params[3:4]
        elif model_name == "RADIAL" and params.size >= 5:
            radial = params[3:5]
        cameras.append({
            "name": img.name,
            "R": R,
            "t": t,
            "K": K,
            "center": c,
            "camera_model": model_name,
            "camera_params": params,
            "radial_distortion": radial,
            "image_size": [int(getattr(cam, "width", 0)), int(getattr(cam, "height", 0))],
            "undistorted": model_name in {"PINHOLE", "SIMPLE_PINHOLE"},
        })
    for pid in recon.points3D:
        point = recon.points3D[pid]
        points.append(point.xyz)
        colors.append(getattr(point, "color", [128, 128, 128]))
        errors.append(float(getattr(point, "error", np.nan)))
        track = getattr(point, "track", None)
        track_lengths.append(len(track.elements) if track is not None and hasattr(track, "elements") else 0)
    finite_errors = np.asarray(errors, dtype=np.float64)
    finite_errors = finite_errors[np.isfinite(finite_errors)]
    return {
        "cameras": cameras,
        "points3D": np.asarray(points, dtype=np.float64).reshape(-1, 3),
        "colors3D": np.asarray(colors, dtype=np.uint8).reshape(-1, 3),
        "quality": {
            "registered_images": len(cameras),
            "points3D": len(points),
            "median_reprojection_error": float(np.median(finite_errors)) if len(finite_errors) else None,
            "mean_reprojection_error": float(np.mean(finite_errors)) if len(finite_errors) else None,
            "mean_track_length": float(np.mean(track_lengths)) if track_lengths else 0.0,
            "component_count": len(recons),
            "component_registered_images": sorted(
                (len(model.images) for model in recons), reverse=True
            ),
        },
        "model_path": model_path,
    }


def _reconstructions(maps) -> list:
    """兼容 pycolmap 不同版本的 mapping 返回形态，并过滤空模型。"""
    if isinstance(maps, dict):
        return [model for model in maps.values() if model is not None]
    if isinstance(maps, (list, tuple)):
        return [model for model in maps if model is not None]
    return [maps] if maps is not None else []
