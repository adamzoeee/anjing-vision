"""3DGS 训练：SLAM3R 恢复位姿的真实视图 + 对齐点云初始化 → INRIA Gaussian PLY。

输入 gaussian/cameras.json + gaussian/images/ + gaussian/init_points.npz
输出 gaussian/gaussian.ply（gsplat.js / GaussianSplats3D 可直接加载）
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image


def build_ssim():
    def gaussian(window_size, sigma):
        g = torch.tensor([math.exp(-(x - window_size // 2) ** 2 / float(2 * sigma ** 2))
                          for x in range(window_size)])
        return g / g.sum()

    g = gaussian(11, 1.5)
    window = g[:, None] * g[None, :]
    window = window.expand(3, 1, 11, 11).contiguous()
    kernel = window.cuda()

    def ssim(img1, img2):
        c1, c2 = 0.01 ** 2, 0.03 ** 2
        mu1 = torch.nn.functional.conv2d(img1, kernel, padding=5, groups=3)
        mu2 = torch.nn.functional.conv2d(img2, kernel, padding=5, groups=3)
        s1 = torch.nn.functional.conv2d(img1 * img1, kernel, padding=5, groups=3) - mu1 ** 2
        s2 = torch.nn.functional.conv2d(img2 * img2, kernel, padding=5, groups=3) - mu2 ** 2
        s12 = torch.nn.functional.conv2d(img1 * img2, kernel, padding=5, groups=3) - mu1 * mu2
        num = (2 * mu1 * mu2 + c1) * (2 * s12 + c2)
        den = (mu1 ** 2 + mu2 ** 2 + c1) * (s1 + s2 + c2)
        return torch.clamp(num / den, 0, 1).mean()

    return ssim


def train(work_dir: Path, iters: int = 8000) -> dict:
    from gsplat import rasterization
    from gsplat.exporter import export_splats
    from gsplat.strategy import DefaultStrategy

    work_dir = Path(work_dir)
    cams = json.loads((work_dir / "cameras.json").read_text(encoding="utf-8"))
    init = np.load(work_dir / "init_points.npz")
    means0 = torch.from_numpy(init["means"].astype(np.float32)).cuda()
    colors0 = torch.from_numpy(init["colors"].astype(np.float32)).cuda()
    n_gauss = means0.shape[0]

    # ---- 相机 ----
    viewmats, Ks = [], []
    for c in cams:
        R_cw = torch.tensor(c["rotation"], dtype=torch.float64)
        C = torch.tensor(c["position"], dtype=torch.float64)
        R_wc = R_cw.T
        t = -R_wc @ C
        vm = torch.eye(4, dtype=torch.float64)
        vm[:3, :3] = R_wc
        vm[:3, 3] = t
        viewmats.append(vm)
        K = torch.eye(3, dtype=torch.float64)
        K[0, 0] = c["fx"]
        K[1, 1] = c["fy"]
        K[0, 2] = c["cx"]
        K[1, 2] = c["cy"]
        Ks.append(K)
    viewmats = torch.stack(viewmats).cuda().float()
    Ks = torch.stack(Ks).cuda().float()
    n_cams = len(cams)
    print(f"train: {n_gauss} gaussians, {n_cams} views", flush=True)

    # ---- 参数（gsplat 1.5：ParameterDict + 每参数独立优化器）----
    init_scale = 0.012  # 对齐点云体素 ~8mm，尺度初始化取 1.2cm
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(means0),
        "quats": torch.nn.Parameter(torch.zeros(n_gauss, 4, device="cuda")),
        "scales": torch.nn.Parameter(torch.log(torch.full((n_gauss, 3), init_scale, device="cuda"))),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n_gauss,), 0.1, device="cuda"))),
        "colors": torch.nn.Parameter(torch.log(torch.clamp(colors0, 1e-6, 1 - 1e-6) / (1 - torch.clamp(colors0, 1e-6, 1 - 1e-6)))),
    })
    params["quats"].data[:, 0] = 1.0
    lrs = {"means": 1.6e-4, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "colors": 2.5e-3}
    optimizers = {
        name: torch.optim.Adam([{"params": [params[name]], "lr": lr, "name": name}], lr=0.0, eps=1e-15)
        for name, lr in lrs.items()
    }
    strategy = DefaultStrategy(verbose=False, refine_stop_iter=7000, refine_every=200)

    ssim_fn = build_ssim()
    state = strategy.initialize_state(scene_scale=1.0)
    strategy.check_sanity(params, optimizers)

    # ---- 图像 ----
    images = []
    for c in cams:
        img = np.asarray(Image.open(work_dir / "images" / f"{c['id']:05d}.jpg")).astype(np.float32) / 255.0
        images.append(torch.from_numpy(img).cuda().permute(2, 0, 1))  # 3,H,W
    H, W = images[0].shape[1], images[0].shape[2]

    started = time.perf_counter()
    rng = np.random.default_rng(0)
    BATCH = 4
    for step in range(iters):
        vids = rng.choice(n_cams, size=BATCH, replace=False).tolist()
        gt = torch.stack([images[v] for v in vids])  # (C,3,H,W)
        vm = viewmats[vids]  # (C,4,4)
        K = Ks[vids]  # (C,3,3)
        quats_n = params["quats"] / params["quats"].norm(dim=1, keepdim=True)
        means_b = params["means"]  # (N,3)
        quats_b = quats_n  # (N,4)
        scales_b = torch.exp(params["scales"])  # (N,3)
        opa_b = torch.sigmoid(params["opacities"])  # (N,)
        col_b = torch.sigmoid(params["colors"])[:, None, :]  # (N,1,3) SH
        renders, alphas, info = rasterization(
            means_b, quats_b, scales_b, opa_b, col_b,
            vm, K, W, H, sh_degree=0, render_mode="RGB",
        )
        pred = renders.permute(0, 3, 1, 2)  # (C,3,H,W)
        l1 = torch.abs(pred - gt).mean()
        ssim_loss = 1.0 - ssim_fn(pred, gt)
        loss = l1 + 0.2 * ssim_loss
        strategy.step_pre_backward(params, optimizers, state, step, info)
        loss.backward()
        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)
        strategy.step_post_backward(params, optimizers, state, step, info, packed=True)
        if step % 500 == 0:
            elapsed = time.perf_counter() - started
            print(f"iter {step}/{iters} loss {loss.item():.4f} "
                  f"gauss {params['means'].shape[0]} t {elapsed:.0f}s", flush=True)

    # ---- 导出（gsplat 1.5: export_splats, sh0 (N,1,3) + 空 shN）----
    with torch.no_grad():
        # 控制 Web 端体量：按不透明度保留最多 1.2M 个高斯
        opa_final = torch.sigmoid(params["opacities"]).detach()
        n_keep = min(1200000, opa_final.numel())
        if opa_final.numel() > n_keep:
            keep_idx = torch.topk(opa_final, n_keep).indices
            means_e = params["means"].detach()[keep_idx]
            scales_e = torch.exp(params["scales"]).detach()[keep_idx]
            quats_e = (params["quats"] / params["quats"].norm(dim=1, keepdim=True)).detach()[keep_idx]
            opa_e = opa_final[keep_idx]
            sh0_e = torch.sigmoid(params["colors"]).detach()[keep_idx][:, None, :]
        else:
            means_e = params["means"].detach()
            scales_e = torch.exp(params["scales"]).detach()
            quats_e = (params["quats"] / params["quats"].norm(dim=1, keepdim=True)).detach()
            opa_e = opa_final
            sh0_e = torch.sigmoid(params["colors"]).detach()[:, None, :]
        shN_e = means_e.new_zeros(means_e.shape[0], 0, 3)
        export_splats(
            means_e, scales_e, quats_e, opa_e, sh0_e, shN_e,
            format="ply", save_to=str(work_dir / "gaussian.ply"),
        )
        # export_splats 保存的是 gsplat 激活值；网页查看器需要 INRIA 的
        # log-scale/logit-opacity/SH 颜色以及 xyzw 四元数表示。
        from convert_web_ply import convert as convert_web_ply
        convert_web_ply(work_dir / "gaussian.ply", work_dir / "gaussian_web.ply")
        from convert_web_ply import convert_splat as convert_web_splat
        convert_web_splat(work_dir / "gaussian.ply", work_dir / "gaussian_web.splat")
    elapsed = time.perf_counter() - started
    print(f"TRAIN_DONE iters={iters} gauss={means_e.shape[0]} seconds={elapsed:.0f}", flush=True)
    return {"gaussian_ply": str(work_dir / "gaussian.ply"), "gaussians": int(means_e.shape[0]),
            "views": n_cams, "seconds": round(elapsed, 1)}


if __name__ == "__main__":
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\32\gaussian")
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    print(train(work, iters))
