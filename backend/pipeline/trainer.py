"""3DGS 训练：gsplat 1.5.x 光栅化 + Adam 优化，输入 SFM 相机位姿与图片。"""
import numpy as np
import torch
import torch.nn.functional as F

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
NUM_ITER = 7000          # RTX 5080 上约 5~15 分钟
SH_DEGREE = 3


def prepare_tensors(cameras: list[dict], images: list[np.ndarray]) -> dict:
    """把位姿/图片转成训练张量。K(相机内参), c2w, imgs（均带 batch 维）。

    支持两种相机约定：
    - SFM 输出：含 "R"(world→cam) 与 "t" 键 → c2w = [[R.T, -R.T@t],[0,1]]
    - 合成相机（build_synthetic_cameras）：含 "R"(c2w 旋转) 与 "center" 键、无 "t" → c2w = [[R, center],[0,1]]
    """
    K = np.stack([c["K"] for c in cameras]).astype(np.float32)
    c2w = []
    for c in cameras:
        if "t" in c:
            R, t = c["R"].astype(np.float32), c["t"].astype(np.float32)
            c2w.append(np.block([[R.T, -R.T @ t.reshape(3, 1)], [0, 0, 0, 1]]))
        else:
            R, center = c["R"].astype(np.float32), np.asarray(c["center"], np.float32)
            c2w.append(np.block([[R, center.reshape(3, 1)], [0, 0, 0, 1]]))
    c2w = np.stack(c2w).astype(np.float32)
    imgs = np.stack([np.asarray(im)[..., :3].astype(np.float32) / 255.0 for im in images])
    return {
        "K": torch.from_numpy(K).to(DEVICE),
        "c2w": torch.from_numpy(c2w).to(DEVICE),
        "imgs": torch.from_numpy(imgs).to(DEVICE),
    }


def train_gaussians(gt: dict, init_points: np.ndarray | None = None,
                    num_iter: int = NUM_ITER) -> dict:
    """训练高斯场，返回 {means, scales, quats, opacities, sh0, sh_rest}（CPU 张量）。

    scales 返回 log 尺度（与 exporter 的 exp() 约定一致），opacities 返回 (N,)。
    """
    from gsplat import rasterization

    n = len(gt["imgs"])
    if init_points is None or len(init_points) < 100:
        init_points = np.random.randn(5000, 3).astype(np.float32)
    means = torch.nn.Parameter(torch.from_numpy(init_points.astype(np.float32)).to(DEVICE))
    scales = torch.nn.Parameter(torch.log(torch.full((len(means), 3), 0.02, device=DEVICE)))
    quats = torch.nn.Parameter(torch.randn(len(means), 4, device=DEVICE))
    quats.data = quats.data / quats.data.norm(dim=1, keepdim=True)
    opacities = torch.nn.Parameter(torch.full((len(means),), 1.0, device=DEVICE))
    sh0 = torch.nn.Parameter(torch.zeros(len(means), 1, 3, device=DEVICE))
    sh_rest = torch.nn.Parameter(torch.zeros(len(means), (SH_DEGREE + 1) ** 2 - 1, 3, device=DEVICE))
    params = [means, scales, quats, opacities, sh0, sh_rest]
    opt = torch.optim.Adam(params, lr=1e-2)

    H, W = gt["imgs"].shape[1], gt["imgs"].shape[2]
    for step in range(num_iter):
        idx = torch.randint(0, n, (1,), device=DEVICE)
        K, c2w, img = gt["K"][idx], gt["c2w"][idx], gt["imgs"][idx]
        colors = torch.cat([sh0, sh_rest], dim=1)
        viewmats = torch.linalg.inv(c2w)
        render, alpha, _ = rasterization(
            means, quats, torch.exp(scales), opacities, colors,
            viewmats, K, W, H, sh_degree=SH_DEGREE, packed=False,
        )
        loss = F.mse_loss(render, img) + 0.01 * (1 - alpha).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        means.data.clamp_(-5, 5)
    return {k: v.detach().cpu() for k, v in
            zip(["means", "scales", "quats", "opacities", "sh0", "sh_rest"], params)}
