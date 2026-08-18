"""多视角视频边界证据修正家具盒子尺寸 —— 长度识别的底层模型修正（点云+视频融合）。

为什么单看点云会量不准（根因）：
  SLAM3R 稠密点云天然不完整——物体靠墙面、背侧面没有点；床沿被床单/被子的
  低纹理面吞掉；桌面薄板点稀疏且与墙粘连。任何纯点云的百分位/SVD 外推都只能
  在“有没有点”之间猜边界，尾部噪声会把盒子拉大，缺面又把盒子截短。
视频为什么是独立证据：
  同一物体在数百帧里被不同角度反复看到，物体轮廓（图像梯度最强处）在每一帧
  的投影里都是其真实边界位置的强约束，且与点云噪声无关。
本模块做什么：
  把拟合出的 3D 盒子的 6 个面投影到多帧；对每个面的每条投影边，沿外法向搜索
  最近的强梯度边界，把像素偏移换算成世界米数；按“面”聚合所有视角的加权中值
  修正量，逐面平移盒子边界（+x/−x/+y/−y/+z 各自独立修正，允许不对称）。
通用性：
  不依赖任何扫描专属常量；相机位姿/图像不存在时上层直接跳过，退化为纯点云。
"""
from __future__ import annotations

import json
import math
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("anjing.pipeline.video_refiner")

MAX_VIEWS_PER_OBJECT = 64        # 每物体最多采样多少帧（600 帧里均匀取）
SEARCH_HALF_M = 0.12             # 在云拟合盒面附近 ±0.12m 内搜索真实边界：
                                 # 点云给出近似位置，视频只做局部锐化，避免
                                 # 锁到旁边家具（床↔书桌互锁）的边界上
MAX_FACE_SHIFT_M = 0.12          # 单面最终修正量上限（与搜索窗一致）
MIN_VIEWS_PER_FACE = 4           # 一面至少需要多少视角证据才修正
MAX_VIEWS_IQR_M = 0.20           # 各视角修正量四分位距超过该值视为不可信
MIN_AXIS_PX = 6.0                # 法向在图像上的投影少于该像素数时该视角对该面无信息
MIN_REGION_IN_FRAME = 40         # 物体区域点落入该帧的数量下限（判定“看得到”）
EDGE_SAMPLE_PX = 2.0             # 边采样步长（像素）
GRADIENT_PEAK_RATIO = 1.6        # 峰值需达到沿线中值的多少倍才算“真边界”


def load_cameras(cameras_json: Path) -> list[dict]:
    cameras = json.loads(Path(cameras_json).read_text(encoding="utf-8"))
    if not cameras:
        raise RuntimeError("cameras.json 为空")
    return cameras


def _select_views(cameras: list[dict], max_views: int) -> list[int]:
    count = len(cameras)
    if count <= max_views:
        return list(range(count))
    indices = np.linspace(0, count - 1, max_views)
    return sorted({int(round(float(i))) for i in indices})


def _project(points_world: np.ndarray, cam: dict) -> tuple[np.ndarray, np.ndarray]:
    """世界(对齐米制系) → 像素坐标。p_cam = R·(p − C)，与 recover_poses 一致。"""
    center = np.asarray(cam["position"], dtype=np.float64)
    rotation = np.asarray(cam["rotation"], dtype=np.float64)
    pc = (points_world - center) @ rotation.T
    z = pc[:, 2]
    u = cam["fx"] * pc[:, 0] / np.maximum(z, 1e-6) + cam["cx"]
    v = cam["fy"] * pc[:, 1] / np.maximum(z, 1e-6) + cam["cy"]
    return np.stack([u, v], axis=1), z


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    """Sobel 梯度幅值（float32），物体轮廓处出现强响应。"""
    try:
        import cv2
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        return np.hypot(gx, gy)
    except ImportError:
        gy, gx = np.gradient(gray.astype(np.float32))
        return np.hypot(gx, gy)


def _sample_profile(gmag: np.ndarray, width: int, height: int,
                    origin: np.ndarray, direction: np.ndarray, search_px: float) -> tuple[float, float]:
    """沿 origin + t·direction 采样梯度，返回 (边界偏移t, 峰值强度)。

    盒边投影可能与真实边界重合（t≈0）、也可能因盒子过长/过短而偏内/偏外，
    因此向两侧搜索，取“离投影边最近、且显著强于沿线中值”的边界。
    """
    if search_px < 2:
        return 0.0, 0.0
    steps = np.arange(-search_px, search_px + 1, EDGE_SAMPLE_PX)
    xs = origin[0] + steps * direction[0]
    ys = origin[1] + steps * direction[1]
    valid = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    if valid.sum() < 5:
        return 0.0, 0.0
    xs, ys, steps = xs[valid], ys[valid], steps[valid]
    profile = gmag[ys.astype(int), xs.astype(int)]
    peak = float(profile.max())
    median = float(np.median(profile))
    if peak < median * GRADIENT_PEAK_RATIO or peak < 1e-6:
        return 0.0, 0.0
    strong = profile >= max(0.6 * peak, median * GRADIENT_PEAK_RATIO)
    nearest = int(np.flatnonzero(strong)[np.argmin(np.abs(steps[strong]))])
    return float(steps[nearest]), peak


def _object_axes(theta: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ax = np.array([math.cos(theta), math.sin(theta), 0.0])
    ay = np.array([-math.sin(theta), math.cos(theta), 0.0])
    az = np.array([0.0, 0.0, 1.0])
    return ax, ay, az


def _refine_object(obj: dict, region: np.ndarray, cameras: list[dict],
                   image_loader, cfg: dict, lo: np.ndarray, hi: np.ndarray) -> tuple[dict, dict]:
    center = np.asarray(obj["center"], dtype=float)
    size = np.asarray(obj["size"], dtype=float)
    theta = math.radians(float(obj.get("rotation_z_deg", 0.0)))
    ax, ay, az = _object_axes(theta)
    half = size / 2

    # 六个面：(轴, 轴向符号, 当前半宽)
    faces = [
        (ax, +1, half[0]), (ax, -1, half[0]),
        (ay, +1, half[1]), (ay, -1, half[1]),
        (az, +1, half[2]), (az, -1, half[2]),
    ]
    # 每个面四条边的两个正交轴（边中点 = 面中心 ± 正交轴半宽）
    ortho = {0: (ay, az, half[1], half[2]), 1: (ay, az, half[1], half[2]),
             2: (ax, az, half[0], half[2]), 3: (ax, az, half[0], half[2]),
             4: (ax, ay, half[0], half[1]), 5: (ax, ay, half[0], half[1])}

    samples: dict[int, list[tuple[float, float]]] = {face_id: [] for face_id in range(6)}
    views_used = 0
    for view in _select_views(cameras, cfg["max_views"]):
        cam = cameras[view]
        center_cam = np.asarray(cam["position"], dtype=np.float64)
        rotation = np.asarray(cam["rotation"], dtype=np.float64)
        width, height = int(cam["width"]), int(cam["height"])

        # 可见性：物体区域点在该帧至少要有 MIN_REGION_IN_FRAME 个
        pc = (region - center_cam) @ rotation.T
        z_region = pc[:, 2]
        front = z_region > 0.05
        if int(front.sum()) < cfg["min_region_in_frame"]:
            continue
        u = cam["fx"] * pc[:, 0] / np.maximum(z_region, 1e-6) + cam["cx"]
        v = cam["fy"] * pc[:, 1] / np.maximum(z_region, 1e-6) + cam["cy"]
        in_frame = front & (u >= 0) & (u < width) & (v >= 0) & (v < height)
        if int(in_frame.sum()) < cfg["min_region_in_frame"]:
            continue
        gray = image_loader(int(cam["id"]))
        if gray is None:
            continue
        gmag = _gradient_magnitude(gray)

        # 盒子中心投影（用于判断边采样方向的“向外”侧）
        box_uv, box_z = _project(center[None, :], cam)
        box_uv, box_z = box_uv[0], float(box_z[0])
        if box_z <= 0.05:
            continue

        view_sees_any = False
        for face_id, (normal, sign, face_half) in enumerate(faces):
            if face_id == 5:
                continue  # 底面贴地，不是可修正的边界（避免把盒子修到地板以下）
            normal = sign * normal
            face_center = center + normal * face_half
            fc_uv, fc_z = _project(face_center[None, :], cam)
            fc_uv, fc_z = fc_uv[0], float(fc_z[0])
            if fc_z <= 0.05 or not (0 <= fc_uv[0] < width and 0 <= fc_uv[1] < height):
                continue
            # 贴墙面（<0.35m）在视频里常被窗帘/窗框/墙根阴影遮挡，任何方向的
            # 修正都不可靠（外扩锁到窗帘、内缩锁到纹理）——该面直接跳过视频修正。
            wall_dist = float(min(
                abs(face_center[0] - lo[0]), abs(face_center[0] - hi[0]),
                abs(face_center[1] - lo[1]), abs(face_center[1] - hi[1]),
            ))
            if wall_dist < 0.35:
                continue
            # 法向在图像上的方向（像素/米）
            d_n_uv, d_n_z = _project((face_center + normal)[None, :], cam)
            d_n_uv = d_n_uv[0] - fc_uv
            axis_px = float(np.linalg.norm(d_n_uv))
            if axis_px < cfg["min_axis_px"] or d_n_z[0] <= 0.05:
                continue
            d_n_unit = d_n_uv / max(axis_px, 1e-9)

            (o1, o2, h1, h2) = ortho[face_id]
            for s1 in (-1.0, 1.0):
                for s2 in (-1.0, 1.0):
                    edge_mid = face_center + s1 * h1 * o1 + s2 * h2 * o2
                    em_uv, em_z = _project(edge_mid[None, :], cam)
                    em_uv, em_z = em_uv[0], float(em_z[0])
                    if em_z <= 0.05 or not (0 <= em_uv[0] < width and 0 <= em_uv[1] < height):
                        continue
                    # 向外侧：边中点相对盒子中心的投影方向，投影到面法向方向上
                    outward = em_uv - box_uv
                    alignment = float(np.dot(outward, d_n_unit))
                    if abs(alignment) < 2.0:
                        continue
                    outward_sign = 1.0 if alignment > 0 else -1.0
                    search_px = max(axis_px * cfg["search_half_m"], 8.0)
                    offset_px, strength = _sample_profile(
                        gmag, width, height, em_uv, outward_sign * d_n_unit, search_px)
                    if strength <= 0:
                        continue
                    shift_m = (outward_sign * offset_px) / axis_px
                    if abs(shift_m) <= cfg["search_half_m"] * 1.5:
                        weight = min(axis_px, 80.0) * math.log1p(strength)
                        samples[face_id].append((shift_m, weight))
                        view_sees_any = True
        if view_sees_any:
            views_used += 1

    report = {"views_used": views_used, "faces": {}, "status": "insufficient_evidence"}
    refined_any = False
    halfs = [half[0], half[1], half[2]]  # 动态半宽：先修的面会影响后修面的中心
    for face_id, (axis_vec, sign, _face_half) in enumerate(faces):
        data = samples[face_id]
        if len(data) < cfg["min_views_per_face"]:
            report["faces"][face_id] = {"samples": len(data), "status": "too_few_views"}
            continue
        shifts = np.asarray([item[0] for item in data])
        weights = np.asarray([item[1] for item in data])
        # 加权中值
        order = np.argsort(shifts)
        cum = np.cumsum(weights[order])
        median_shift = float(shifts[order][np.searchsorted(cum, cum[-1] / 2)])
        iqr = float(np.percentile(shifts, 75) - np.percentile(shifts, 25))
        if iqr > cfg["max_views_iqr_m"]:
            report["faces"][face_id] = {"samples": len(data), "status": "disagreement",
                                        "shift_m": round(median_shift, 3), "iqr_m": round(iqr, 3)}
            continue
        shift = float(np.clip(median_shift, -cfg["max_face_shift_m"], cfg["max_face_shift_m"]))
        if abs(shift) < 0.01:
            report["faces"][face_id] = {"samples": len(data), "status": "confirmed",
                                        "shift_m": round(shift, 3)}
            continue
        normal = sign * axis_vec
        axis_id = face_id // 2
        new_half = halfs[axis_id] + shift
        if new_half < 0.03:
            report["faces"][face_id] = {"samples": len(data), "status": "rejected_thin",
                                        "shift_m": round(shift, 3)}
            continue
        # 更新盒：面沿自身法向平移 shift，中心平移 shift/2（对侧面保持不动）
        report["faces"][face_id] = {"samples": len(data), "status": "shifted",
                                    "shift_m": round(shift, 3), "iqr_m": round(iqr, 3)}
        center = center + normal * shift / 2
        halfs[axis_id] = new_half
        refined_any = True
    if refined_any:
        report["status"] = "refined"
    obj = dict(obj)
    obj["center"] = center.tolist()
    obj["size"] = (np.asarray(halfs) * 2).tolist()
    obj["video_refinement"] = report
    return obj, report


def refine_objects(objects: list[dict], aligned_points: np.ndarray,
                   cameras_json: Path, images_dir: Path, *,
                   small_skip: bool = True) -> list[dict]:
    """对每个已验证家具盒子做多视角视频边界修正（原地替换 center/size）。

    只修正大件（床/柜/桌/沙发等）；小件轮廓太弱且投影面积小，跳过。
    """
    cameras = load_cameras(cameras_json)
    images_dir = Path(images_dir)
    cfg = {
        "max_views": MAX_VIEWS_PER_OBJECT,
        "search_half_m": SEARCH_HALF_M,
        "min_views_per_face": MIN_VIEWS_PER_FACE,
        "max_views_iqr_m": MAX_VIEWS_IQR_M,
        "max_face_shift_m": MAX_FACE_SHIFT_M,
        "min_axis_px": MIN_AXIS_PX,
        "min_region_in_frame": MIN_REGION_IN_FRAME,
    }
    image_files = sorted(images_dir.glob("*.jpg"))
    if not image_files:
        raise RuntimeError("images 目录无帧图像")
    import cv2  # 后端 venv 已装；无则上层跳过本模块

    def image_loader(cam_id: int):
        candidate = images_dir / f"{int(cam_id):05d}.jpg"
        if not candidate.is_file():
            return None
        return cv2.imread(str(candidate), cv2.IMREAD_GRAYSCALE)

    SMALL = {"stool", "chair", "bin", "trash_bin", "box", "small_table", "lamp", "suitcase"}
    refined: list[dict] = []
    base_cfg = dict(cfg)
    # 房间边界（贴墙面判定用）
    room_lo = np.array([np.percentile(aligned_points[:, 0], 1), np.percentile(aligned_points[:, 1], 1)])
    room_hi = np.array([np.percentile(aligned_points[:, 0], 99), np.percentile(aligned_points[:, 1], 99)])
    for obj in objects:
        label = str(obj.get("label", ""))
        if small_skip and label in SMALL:
            refined.append(obj)
            continue
        cfg = dict(base_cfg)
        # 大件（床/柜/沙发）点云常缺整个端面，允许视频在 ±0.25m 内找回边界；
        # 小件（桌/椅）保持 ±0.12m 窄窗，避免锁到相邻家具。
        if label in {"bed", "wardrobe", "cabinet", "bookshelf", "sofa", "tv_stand"}:
            cfg["search_half_m"] = 0.25
        center = np.asarray(obj["center"], dtype=float)
        size = np.asarray(obj["size"], dtype=float)
        theta = math.radians(float(obj.get("rotation_z_deg", 0.0)))
        ax, ay, _ = _object_axes(theta)
        delta = aligned_points - center
        lx = delta[:, 0] * ax[0] + delta[:, 1] * ax[1]
        ly = delta[:, 0] * ay[0] + delta[:, 1] * ay[1]
        margin = 0.18
        region_mask = (
            (np.abs(lx) < size[0] / 2 + margin) & (np.abs(ly) < size[1] / 2 + margin)
            & (delta[:, 2] > 0.05) & (delta[:, 2] < size[2] + 0.20)
        )
        region = aligned_points[region_mask]
        if len(region) < 200:
            obj = dict(obj)
            obj["video_refinement"] = {"status": "insufficient_point_region"}
            refined.append(obj)
            continue
        try:
            new_obj, report = _refine_object(obj, region, cameras, image_loader, cfg, room_lo, room_hi)
            logger.info("video_refine label=%s views=%d status=%s faces=%s",
                        label, report["views_used"], report["status"],
                        {k: v.get("shift_m") for k, v in report["faces"].items() if v.get("shift_m") is not None})
            refined.append(new_obj)
        except Exception as exc:  # noqa: BLE001 - 单物体失败不影响整体
            logger.warning("video_refine_object_failed label=%s reason=%s", label, str(exc)[:200])
            obj = dict(obj)
            obj["video_refinement"] = {"status": "failed", "reason": str(exc)[:200]}
            refined.append(obj)
    return refined
