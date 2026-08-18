"""从 SLAM3R preds 恢复每帧相机位姿（相机系点云 ↔ 融合世界点云的 RANSAC 刚体对齐）。

local_pcds（I2P 相机系，逐关键帧）→ 与 frames_recon.ply（世界系融合点云）做
最近邻匹配 + RANSAC Kabsch，得到每帧 world←cam 刚体变换，再变换到
后处理对齐/米制坐标系，输出 gsplat 训练所需的 cameras.json + 图像 + 初始化点。

相机内参约定：fx≈fy≈280（由 l2w 点云实测），cx=cy=112（224×224）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

H = W = 224


def kabsch(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ca = src.mean(0)
    cb = dst.mean(0)
    hh = (src - ca).T @ (dst - cb)
    uu, _, vt = np.linalg.svd(hh)
    R = vt.T @ uu.T
    if np.linalg.det(R) < 0:
        vt[-1] *= -1
        R = vt.T @ uu.T
    return R, cb - R @ ca


def rigid_from_ransac(src: np.ndarray, dst: np.ndarray, thr: float = 0.04,
                      max_iter: int = 400, min_inl: int = 500) -> tuple | None:
    """纯 numpy RANSAC Kabsch（3 点最小解）src→dst。返回 (R, t, inliers, rms) 或 None。"""
    n = len(src)
    rng = np.random.default_rng(7)
    best = None
    for _ in range(max_iter):
        tri = rng.choice(n, 3, replace=False)
        R, t = kabsch(src[tri], dst[tri])
        d = np.linalg.norm((src @ R.T + t) - dst, axis=1)
        inl = int((d < thr).sum())
        if inl >= min_inl and (best is None or inl > best[2]):
            best = (R, t, inl)
            if inl > n * 0.85:  # 足够好提前退出
                break
    if best is None:
        return None
    R, t, _ = best
    d = np.linalg.norm((src @ R.T + t) - dst, axis=1)
    inl = d < thr
    R2, t2 = kabsch(src[inl], dst[inl])
    d2 = np.linalg.norm((src @ R2.T + t2) - dst, axis=1)
    inl2 = d2 < thr
    rms = float(np.sqrt((d2[inl2] ** 2).mean()))
    return R2, t2, int(inl2.sum()), rms


def recover_poses(preds_dir: Path, out_dir: Path, alignment_json: Path, raw_ply: Path,
                  min_conf_local: float = 2.0, min_conf_reg: float = 12.0,
                  max_frames: int = 400) -> dict:
    preds_dir = Path(preds_dir)
    out_dir = Path(out_dir)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    lp = np.load(preds_dir / "local_pcds.npy", mmap_mode="r")
    lc = np.load(preds_dir / "local_confs.npy", mmap_mode="r")
    rp = np.load(preds_dir / "registered_pcds.npy", mmap_mode="r")
    rc = np.load(preds_dir / "registered_confs.npy", mmap_mode="r")
    imgs = np.load(preds_dir / "input_imgs.npy", mmap_mode="r")
    n_frames = lp.shape[0]

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

    tree = cKDTree(raw_pts)

    cameras = []
    rejected = 0
    for i in range(n_frames):
        loc = np.asarray(lp[i], dtype=np.float64).reshape(-1, 3)
        reg = np.asarray(rp[i], dtype=np.float64).reshape(-1, 3)
        lconf = np.asarray(lc[i], dtype=np.float32).reshape(-1)
        rconf = np.asarray(rc[i], dtype=np.float32).reshape(-1)
        m = (np.isfinite(loc).all(axis=1) & np.isfinite(reg).all(axis=1)
             & (lconf > min_conf_local) & (rconf > min_conf_reg))
        if m.sum() < 3000:
            rejected += 1
            continue
        idx = np.where(m)[0]
        if len(idx) > 20000:
            idx = np.random.default_rng(i).choice(idx, 20000, replace=False)
        src = loc[idx].astype(np.float64)
        dst = reg[idx].astype(np.float64)
        # 第一步：local→registered（同一像素对应）RANSAC 刚体，消除 i2p/l2w 深度分歧
        out = rigid_from_ransac(src, dst)
        if out is None:
            rejected += 1
            continue
        R_wc, T_wc, n_inl, rms0 = out
        # 第二步：变换后与世界融合点云做 NN 匹配，再 Kabsch 精修（真正刚体约束）
        world_est = src @ R_wc.T + T_wc
        dist, nn = tree.query(world_est, k=1, workers=-1)
        good = dist < 0.03
        if good.sum() < 800:
            rejected += 1
            continue
        R2, t2 = kabsch(src[good], raw_pts[nn[good]])
        R_wc, T_wc = R2, t2
        resid = np.linalg.norm((src[good] @ R_wc.T + T_wc) - raw_pts[nn[good]], axis=1)
        rms = float(resid.mean())
        n_inl = int(good.sum())
        if rms > 0.02 or n_inl < 800:
            rejected += 1
            continue
        # 米制变换
        R_metric = R_wc @ R_total.T
        C_metric = -s * (R_total @ (R1.T @ t + R_wc.T @ T_wc))
        cameras.append({
            "id": i, "width": W, "height": H, "fx": 280.0, "fy": 280.0, "cx": 112.0, "cy": 112.0,
            "position": C_metric.tolist(),
            "rotation": R_metric.tolist(),  # cam→world
            "inliers": int(n_inl), "rms_m": round(rms, 4), "rms0_m": round(rms0, 4),
        })
        img = np.asarray(imgs[i])
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        cv2.imwrite(str(out_dir / "images" / f"{i:05d}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        if len(cameras) >= max_frames:
            break

    if len(cameras) < 30:
        raise RuntimeError(f"刚体对齐恢复位姿不足 30 帧（got {len(cameras)}, rejected {rejected}）")
    (out_dir / "cameras.json").write_text(
        json.dumps(cameras, ensure_ascii=False, indent=1), encoding="utf-8")

    aligned_pcd = o3d.io.read_point_cloud(str(out_dir.parent / "postprocess" / "scene_aligned.ply"))
    pts = np.asarray(aligned_pcd.points, dtype=np.float32)
    col = np.asarray(aligned_pcd.colors, dtype=np.float32)
    if len(pts) > 450000:
        keep = np.random.default_rng(42).choice(len(pts), 450000, replace=False)
        pts, col = pts[keep], col[keep]
    np.savez_compressed(out_dir / "init_points.npz", means=pts, colors=col)

    rms = [c["rms_m"] for c in cameras]
    return {"cameras": len(cameras), "median_rms_m": round(float(np.median(rms)), 4),
            "cameras_json": str(out_dir / "cameras.json")}


if __name__ == "__main__":
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\32")
    preds = work / "slam3r" / "scene" / "preds"
    out = work / "gaussian"
    result = recover_poses(preds, out, work / "postprocess" / "alignment.json",
                           work / "frames_recon.ply")
    print(json.dumps(result, ensure_ascii=False))
