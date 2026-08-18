"""用训练好的 gaussian.ply 渲染验证帧，与真实训练图对比（PSNR/SSIM）。"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\32\gaussian")

from gsplat import rasterization  # noqa: E402

def load_ply_gaussians(path, device):
    import struct
    data = Path(path).read_bytes()
    end = data.find(b"end_header") + len(b"end_header\n")
    header = data[:end].decode()
    props = []
    for line in header.splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "property":
            props.append(p[2])
    body = data[end:]
    stride = len(props) * 4
    n = len(body) // stride
    arr = np.frombuffer(body[: n * stride], dtype=np.float32).reshape(n, len(props))
    def col(name):
        return torch.from_numpy(arr[:, props.index(name)].astype(np.float32)).to(device)
    xyz = torch.stack([col("x"), col("y"), col("z")], dim=1)
    scales = torch.stack([col("scale_0"), col("scale_1"), col("scale_2")], dim=1)
    quats = torch.stack([col("rot_0"), col("rot_1"), col("rot_2"), col("rot_3")], dim=1)
    opa = col("opacity")
    sh0 = torch.stack([col("f_dc_0"), col("f_dc_1"), col("f_dc_2")], dim=1)
    return xyz, quats, scales, opa, sh0

def gaussian_kernel(size=11, sigma=1.5):
    g = torch.tensor([np.exp(-((x - size // 2) ** 2) / (2 * sigma ** 2)) for x in range(size)], dtype=torch.float32)
    g /= g.sum()
    return (g[:, None] * g[None, :]).expand(3, 1, size, size).contiguous().cuda()

means, quats, scales, opa, sh0 = load_ply_gaussians(work / "gaussian.ply", "cuda")
cams = json.loads((work / "cameras.json").read_text(encoding="utf-8"))
means = means.contiguous(); quats = quats.contiguous(); scales = scales.contiguous()
opa = opa.contiguous(); sh0 = sh0[:, None, :].contiguous()
kernel = gaussian_kernel()

psnrs, ssims = [], []
for cid in (0, 50, 100, 150, 200):
    c = cams[cid]
    gt = np.asarray(Image.open(work / "images" / f"{c['id']:05d}.jpg")).astype(np.float32) / 255.0
    gt_t = torch.from_numpy(gt).cuda().permute(2, 0, 1)[None]
    R_cw = torch.tensor(c["rotation"], dtype=torch.float64)
    C = torch.tensor(c["position"], dtype=torch.float64)
    R_wc = R_cw.T
    vm = torch.eye(4, dtype=torch.float64)
    vm[:3, :3] = R_wc
    vm[:3, 3] = -R_wc @ C
    K = torch.tensor([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]], dtype=torch.float64)
    renders, _, _ = rasterization(means, quats, scales, opa, sh0,
                                  vm[None].float().cuda(), K[None].float().cuda(),
                                  224, 224, sh_degree=0, render_mode="RGB")
    pred = renders[0].permute(2, 0, 1)[None]  # 1,3,H,W
    mse = ((pred - gt_t) ** 2).mean().item()
    psnr = 10 * np.log10(1.0 / max(mse, 1e-8))
    mu1 = torch.nn.functional.conv2d(pred, kernel, padding=5, groups=3)
    mu2 = torch.nn.functional.conv2d(gt_t, kernel, padding=5, groups=3)
    s1 = torch.nn.functional.conv2d(pred * pred, kernel, padding=5, groups=3) - mu1 ** 2
    s2 = torch.nn.functional.conv2d(gt_t * gt_t, kernel, padding=5, groups=3) - mu2 ** 2
    s12 = torch.nn.functional.conv2d(pred * gt_t, kernel, padding=5, groups=3) - mu1 * mu2
    ssim = ((2 * mu1 * mu2 + 1e-4) * (2 * s12 + 9e-4) / ((mu1 ** 2 + mu2 ** 2 + 1e-4) * (s1 + s2 + 9e-4))).mean().item()
    psnrs.append(psnr); ssims.append(ssim)
    print(f"cam {cid} psnr {psnr:.2f} ssim {ssim:.3f}")
print(f"MEAN psnr {np.mean(psnrs):.2f} ssim {np.mean(ssims):.3f}")
