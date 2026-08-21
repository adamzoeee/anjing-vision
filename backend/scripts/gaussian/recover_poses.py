"""从 SLAM3R preds 恢复每帧相机位姿（世界系点云 + 像素 PnP）。

SLAM3R 保存的 registered_pcds 是「逐帧已注册到统一世界坐标系的点云」
（与最终融合点云吻合到毫米级）。每帧 = 世界 3D 点 + 224×224 像素坐标，
直接 PnP 求解相机位姿；焦距用逐帧线性估计 + 全局中位数，再统一重解一次。

输出 gaussian/cameras.json（米制对齐坐标系）+ 训练图 + 高斯初始化点。
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d

H = W = 224
MAX_SAFE_VIEWS_8GB = 1024


def _image_sharpness(image: np.ndarray) -> float:
    """低成本清晰度指标；仅用于同一视频内候选帧择优。"""
    image = np.asarray(image)
    if image.ndim == 3:
        image = cv2.cvtColor(image.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(image.astype(np.uint8), cv2.CV_32F).var())


def select_distributed_candidates(collected: list[tuple], images, max_frames: int) -> tuple[list[tuple], list[tuple]]:
    """从整段时间均匀选位姿，每个时间桶优先取清晰且PnP质量高的帧。

    返回（主候选，备用候选）。二次PnP若淘汰主候选，备用候选继续补足；
    输出上限仍为 max_frames，不增加 Gaussian 显存规模。
    """
    if max_frames <= 0:
        return [], list(collected)
    if len(collected) <= max_frames:
        return list(collected), []

    def score(candidate: tuple) -> float:
        frame_id, _rvec, _tvec, inliers, _fx, reproj = candidate
        sharpness = _image_sharpness(np.asarray(images[int(frame_id)]))
        return 0.25 * np.log1p(sharpness) + 0.15 * np.log1p(len(inliers)) - float(reproj)

    # collected 已按时间排序；把完整时间线切成 max_frames 个桶，每桶选一个最佳帧。
    edges = np.linspace(0, len(collected), max_frames + 1, dtype=int)
    primary = []
    primary_ids = set()
    scores = {}
    for start, end in zip(edges[:-1], edges[1:]):
        bucket = collected[int(start):int(end)]
        if not bucket:
            continue
        best = max(bucket, key=lambda item: scores.setdefault(int(item[0]), score(item)))
        primary.append(best)
        primary_ids.add(int(best[0]))
    # 备用帧也按质量排序；只在主候选二次PnP失败时才会进入最终上限。
    backups = [item for item in collected if int(item[0]) not in primary_ids]
    backups.sort(key=lambda item: scores.setdefault(int(item[0]), score(item)), reverse=True)
    primary.sort(key=lambda item: int(item[0]))
    return primary, backups


def estimate_focal_from_world(pts_w, conf, min_conf=12.0):
    """从世界系点云反推 fx 近似值（仅用于初值）：与局部相机系无关，
    这里只返回 None——焦距初值固定 280，最终由 PnP 内点线性精修。"""
    return None


def recover_poses(preds_dir: Path, out_dir: Path, alignment_json: Path, raw_ply: Path,
                  min_conf: float = 12.0, max_reproj: float = 2.5,
                  max_frames: int = MAX_SAFE_VIEWS_8GB) -> dict:
    preds_dir = Path(preds_dir)
    out_dir = Path(out_dir)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    rp = np.load(preds_dir / "registered_pcds.npy", mmap_mode="r")
    rc = np.load(preds_dir / "registered_confs.npy", mmap_mode="r")
    imgs = np.load(preds_dir / "input_imgs.npy", mmap_mode="r")
    n_frames = rp.shape[0]

    # ---- 米制变换（与 scene_postprocess 一致）----
    align = json.loads(alignment_json.read_text(encoding="utf-8"))
    R_total = np.asarray(align["alignment"]["rotation"], dtype=np.float64)
    theta = float(np.radians(align["alignment"]["wall_theta_deg"]))
    rz = np.array([[np.cos(-theta), -np.sin(-theta), 0],
                   [np.sin(-theta), np.cos(-theta), 0],
                   [0, 0, 1]], dtype=np.float64)
    R1 = rz.T @ R_total
    s = float(align["scale"]["applied"])

    raw_pcd = o3d.io.read_point_cloud(str(raw_ply))
    raw_pts = np.asarray(raw_pcd.points, dtype=np.float64)
    p1 = raw_pts @ R1.T
    low = p1[p1[:, 2] < np.percentile(p1[:, 2], 35)]
    A = np.column_stack([low[:, :2], np.ones(len(low))])
    b = -low[:, 2]
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    res = np.abs(A @ coef - b)
    keep = res < np.percentile(res, 90)
    coef, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
    floor_z = float(coef[2])
    t = np.array([0.0, 0.0, floor_z])

    u_grid = (np.arange(W, dtype=np.float32)[None, :].repeat(H, 0)).reshape(-1)
    v_grid = (np.arange(H, dtype=np.float32)[:, None].repeat(W, 1)).reshape(-1)

    # ---- 第一遍：fx=280 初值 PnP，逐帧精修 fx ----
    collected = []  # (i, rvec, tvec, inl_idx, fx_refined, med_err)
    fxs = []
    for i in range(n_frames):
        pts = np.asarray(rp[i], dtype=np.float64).reshape(-1, 3)
        conf = np.asarray(rc[i], dtype=np.float32).reshape(-1)
        m = np.isfinite(pts).all(axis=1) & (conf > min_conf)
        if m.sum() < 3000:
            continue
        idx = np.where(m)[0]
        if len(idx) > 15000:
            idx = np.random.default_rng(i).choice(idx, 15000, replace=False)
        obj = pts[idx].astype(np.float64)
        img = np.stack([u_grid[idx], v_grid[idx]], axis=1).astype(np.float64)
        K = np.array([[280.0, 0, W / 2], [0, 280.0, H / 2], [0, 0, 1]], dtype=np.float64)
        ok, rvec, tvec, inl = cv2.solvePnPRansac(
            obj, img, K, None, flags=cv2.SOLVEPNP_EPNP,
            reprojectionError=4.0, confidence=0.99, iterationsCount=300,
        )
        if not ok or inl is None or len(inl) < 800:
            continue
        inl = inl.reshape(-1)
        # 用内点反推 fx：u = fx * X/Z + cx
        R_wc, _ = cv2.Rodrigues(rvec)
        cam = obj[inl] @ R_wc.T + tvec.reshape(3)
        fx_est = np.median((img[inl, 0] - W / 2) * cam[:, 2] / (cam[:, 0] + 1e-9))
        fy_est = np.median((img[inl, 1] - H / 2) * cam[:, 2] / (cam[:, 1] + 1e-9))
        if not (100 < fx_est < 800):
            continue
        fxs.append(float(fx_est))
        proj, _ = cv2.projectPoints(obj[inl], rvec, tvec, K, None)
        med_err = float(np.median(np.linalg.norm(proj.reshape(-1, 2) - img[inl], axis=1)))
        if med_err > max_reproj:
            continue
        collected.append((i, rvec, tvec, inl, fx_est, med_err))
    if len(fxs) < 30:
        raise RuntimeError(f"PnP 位姿不足 30 帧（got {len(fxs)}）")

    fx_global = float(np.median(fxs))
    fx_global = min(max(fx_global, 200.0), 420.0)
    print(f"global fx={fx_global:.1f} from {len(fxs)} frames", flush=True)

    # ---- 第二遍：全局 fx 重解；上限内全保留，超限才从整段均匀择优 ----
    cameras = []
    K = np.array([[fx_global, 0, W / 2], [0, fx_global, H / 2], [0, 0, 1]], dtype=np.float64)
    primary, backups = select_distributed_candidates(collected, imgs, max_frames)
    for (i, _rvec, _tvec, inl, _fx, _err) in [*primary, *backups]:
        pts = np.asarray(rp[i], dtype=np.float64).reshape(-1, 3)
        conf = np.asarray(rc[i], dtype=np.float32).reshape(-1)
        m = np.isfinite(pts).all(axis=1) & (conf > min_conf)
        idx = np.where(m)[0]
        obj = pts[idx].astype(np.float64)
        img = np.stack([u_grid[idx], v_grid[idx]], axis=1).astype(np.float64)
        ok, rvec, tvec, inl2 = cv2.solvePnPRansac(
            obj, img, K, None, flags=cv2.SOLVEPNP_EPNP,
            reprojectionError=4.0, confidence=0.99, iterationsCount=300,
        )
        if not ok or inl2 is None or len(inl2) < 800:
            continue
        inl2 = inl2.reshape(-1)
        R_wc, _ = cv2.Rodrigues(rvec)
        T_wc = tvec.reshape(3)
        proj, _ = cv2.projectPoints(obj[inl2], rvec, tvec, K, None)
        med_err = float(np.median(np.linalg.norm(proj.reshape(-1, 2) - img[inl2], axis=1)))
        if med_err > max_reproj:
            continue
        # 米制变换
        R_metric = R_wc @ R_total.T
        C_metric = -s * (R_total @ (R1.T @ t + R_wc.T @ T_wc))
        cameras.append({
            "id": i, "width": W, "height": H, "fx": fx_global, "fy": fx_global,
            "cx": W / 2, "cy": H / 2,
            "position": C_metric.tolist(),
            "rotation": R_metric.tolist(),
            "inliers": int(len(inl2)), "reproj_px": round(med_err, 3),
        })
        img = np.asarray(imgs[i])
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        cv2.imwrite(str(out_dir / "images" / f"{i:05d}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        if len(cameras) >= max_frames:
            break

    if len(cameras) < 30:
        raise RuntimeError(f"PnP 恢复位姿不足 30 帧（got {len(cameras)}）")
    (out_dir / "cameras.json").write_text(
        json.dumps(cameras, ensure_ascii=False, indent=1), encoding="utf-8")

    aligned_pcd = o3d.io.read_point_cloud(str(out_dir.parent / "postprocess" / "scene_aligned.ply"))
    pts = np.asarray(aligned_pcd.points, dtype=np.float32)
    col = np.asarray(aligned_pcd.colors, dtype=np.float32)
    if len(pts) > 450000:
        keep = np.random.default_rng(42).choice(len(pts), 450000, replace=False)
        pts, col = pts[keep], col[keep]
    np.savez_compressed(out_dir / "init_points.npz", means=pts, colors=col)

    errs = [c["reproj_px"] for c in cameras]
    camera_ids = [int(camera["id"]) for camera in cameras]
    return {"cameras": len(cameras), "fx": fx_global,
            "median_reproj_px": round(float(np.median(errs)), 3),
            "selection": "full_timeline_stratified_quality",
            "source_frame_range": [min(camera_ids), max(camera_ids)],
            "cameras_json": str(out_dir / "cameras.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("work", nargs="?", type=Path, default=Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\32"))
    parser.add_argument("--max-frames", type=int, default=MAX_SAFE_VIEWS_8GB)
    args = parser.parse_args()
    work = args.work
    preds = work / "slam3r" / "scene" / "preds"
    out = work / "gaussian"
    raw_candidates = sorted(work.rglob("*_recon.ply"))
    if not raw_candidates:
        raise RuntimeError(f"未找到 SLAM3R 融合点云 *_recon.ply（{work}）")
    result = recover_poses(preds, out, work / "postprocess" / "alignment.json",
                           raw_candidates[-1],
                           max_frames=min(max(args.max_frames, 30), MAX_SAFE_VIEWS_8GB))
    print(json.dumps(result, ensure_ascii=False))
